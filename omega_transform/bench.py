# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Model benchmark: the same time-boxed search per (model, example).

Each run's summary is written as one JSON file (``bench/results/``) meant to
be committed: the documentation's benchmark page is rendered from these files
(``omega_transform.report``), so published charts always come from committed
numbers. Run directories with the full history go to ``bench/runs/`` and stay
local.
"""
from __future__ import annotations

import difflib
import json
import platform
import re
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .config import ModelSpec, load_config
from .database import Database
from .memory import wait_until_fits
from .modelcheck import unload

RESULT_SCHEMA = 3  # 2: best_lineage and best_diff; 3: initial_solution and best_solution
# The best program's diff is cut after this many lines (a rewrite can be long).
MAX_DIFF_LINES = 400


def installed_models(base_url: str) -> list[dict]:
    """Chat-capable models known to the Ollama server, smallest first."""
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=10) as r:
        models = json.loads(r.read()).get("models", [])
    chat = [m for m in models if "embed" not in m["name"]
            and "completion" in (m.get("capabilities") or ["completion"])]
    unique: dict[str, dict] = {}  # several tags can name the same weights
    for m in sorted(chat, key=lambda m: (len(m["name"]), m["name"])):
        unique.setdefault(m.get("digest") or m["name"], m)
    return sorted(unique.values(), key=lambda m: m.get("size", 0))


def openrouter_models(names: list[str]) -> list[dict]:
    """Model entries for OpenRouter models, shaped like Ollama's (no size: nothing is loaded)."""
    from .llm import OPENROUTER_URL, urlopen
    try:
        with urlopen(f"{OPENROUTER_URL}/models", timeout=15) as r:
            listed = {m["id"]: m for m in json.loads(r.read()).get("data", [])}
    except (OSError, ValueError):
        listed = {}
    out = []
    for n in names:
        info = listed.get(n, {})
        caps = ["completion"] + (["thinking"] if "reasoning" in
                                 (info.get("supported_parameters") or []) else [])
        out.append({"name": n, "provider": "openrouter", "size": 0, "capabilities": caps,
                    "details": {"parameter_size": ""}})
    return out


def loaded_model_memory(base_url: str, name: str) -> dict:
    """Memory of a loaded model as Ollama reports it (weights, KV cache, buffers)."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/ps", timeout=10) as r:
            for m in json.loads(r.read()).get("models", []):
                if m.get("name") == name or m.get("model") == name:
                    return {"model_memory_gb": round(m.get("size", 0) / 1e9, 1),
                            "model_gpu_memory_gb": round(m.get("size_vram", 0) / 1e9, 1),
                            "context_length": m.get("context_length")}
    except (OSError, ValueError):
        pass
    return {}


def inference_summary(calls: list[dict]) -> dict:
    """Speed of the model itself, from the per-call stats the server reported."""
    if not calls:
        return {}
    totals = sorted(c["total_seconds"] for c in calls)

    def pct(q: float) -> float:
        return round(totals[min(len(totals) - 1, int(q * len(totals)))], 1)

    def rate(tokens: str, seconds: str) -> float | None:
        t = sum(c[tokens] or 0 for c in calls)
        s = sum(c[seconds] or 0 for c in calls)
        return round(t / s, 1) if s > 0 else None

    done = [c for c in calls if not c["stopped"]]
    cost = sum(c.get("cost_usd") or 0 for c in calls)
    return {
        "cost_usd": round(cost, 4),
        "load_seconds": round(max(c["load_seconds"] or 0 for c in calls), 1),
        "median_call_seconds": pct(0.5),
        "p90_call_seconds": pct(0.9),
        "prompt_tokens_per_s": rate("prompt_tokens", "prompt_seconds"),
        "gen_tokens_per_s": rate("gen_tokens", "gen_seconds"),
        "mean_prompt_tokens": round(sum(c["prompt_tokens"] for c in done) / max(1, len(done))),
        "mean_gen_tokens": round(sum(c["gen_tokens"] for c in done) / max(1, len(done))),
        "stopped_calls": sum(1 for c in calls if c["stopped"]),
    }


def machine_info() -> dict:
    def sysctl(key: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except OSError:
            return ""
    mem = sysctl("hw.memsize")
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "model": sysctl("hw.model"),
        "cores": sysctl("hw.ncpu"),
        "memory_gb": round(int(mem) / 2**30) if mem.isdigit() else None,
        "python": platform.python_version(),
    }


def git_commit(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except OSError:
        return ""


@dataclass
class BenchResult:
    schema: int
    example: str
    model: str
    variant: str  # "think", "no-think" or "default": the model's reasoning mode
    model_size_gb: float
    model_params: str
    started: str
    minutes_budget: float
    wall_minutes: float
    omega_version: str
    omega_commit: str
    machine: dict
    settings: dict
    initial_score: float | None = None
    best_score: float | None = None
    improvement: float | None = None  # best / initial
    llm_calls: int = 0
    mean_llm_seconds: float = 0.0
    attempts: int = 0
    ok: int = 0
    statuses: dict = field(default_factory=dict)
    sweeps: int = 0
    sweep_variants: int = 0
    improvements: int = 0
    minutes_to_best: float | None = None
    best_idea: str = ""
    progress: list = field(default_factory=list)  # [[minutes, best score], ...]
    # How the best program came about: its ancestors from the initial program on,
    # [{minutes, score, model, mode, status, idea}, ...], and its diff to the initial program.
    best_lineage: list = field(default_factory=list)
    best_diff: str = ""
    # What the initial and the best program produced, in the evaluator's drawable form
    # (its "solution"); None when the evaluator gives none. Drawn on the example pages.
    initial_solution: dict | None = None
    best_solution: dict | None = None
    error: str = ""
    # inference: how fast the model is and what it costs in memory
    model_memory_gb: float | None = None
    model_gpu_memory_gb: float | None = None
    context_length: int | None = None
    load_seconds: float | None = None
    median_call_seconds: float | None = None
    p90_call_seconds: float | None = None
    prompt_tokens_per_s: float | None = None
    gen_tokens_per_s: float | None = None
    mean_prompt_tokens: int | None = None
    mean_gen_tokens: int | None = None
    stopped_calls: int = 0
    provider: str = "ollama"
    cost_usd: float = 0.0  # what the run's model requests cost (OpenRouter); 0 for local models


def summarize(db: Database) -> dict:
    """The comparable numbers of one finished run."""
    progs = db.all_programs()
    if not progs:
        return {}
    root = next((p for p in progs if p.mode == "initial"), None)
    t0 = min(p.created for p in progs)
    answers = [p for p in progs if p.mode != "initial" and p.status != "swept"
               and not p.mode.startswith("sweep/")]
    llm = [p for p in answers if p.llm_seconds]
    statuses: dict[str, int] = {}
    for p in answers:
        statuses[p.status] = statuses.get(p.status, 0) + 1
    progress, best, improvements, best_prog = [], None, 0, root
    for p in sorted(progs, key=lambda p: p.created):
        if p.ok and (best is None or p.score > best):
            if best is not None:
                improvements += 1
            best, best_prog = p.score, p
            progress.append([round((p.created - t0) / 60, 2), p.score])
    initial = root.score if root else None
    return {
        "initial_score": initial,
        "best_score": best,
        "improvement": (best / initial) if best and initial else None,
        "llm_calls": len(llm),
        "mean_llm_seconds": round(sum(p.llm_seconds for p in llm) / max(1, len(llm)), 1),
        "attempts": len(answers),
        "ok": statuses.get("ok", 0),
        "statuses": statuses,
        "sweeps": len({p.parent_id for p in progs if p.status == "swept"}),
        "sweep_variants": sum(1 for p in progs if p.status == "swept" or p.mode.startswith("sweep/")),
        "improvements": improvements,
        "minutes_to_best": progress[-1][0] if len(progress) > 1 else None,
        "best_idea": (best_prog.idea if best_prog else "")[:200],
        "progress": progress,
        **best_details(db, best_prog, root, t0),
        "initial_solution": db.solution(root.id) if root else None,
        "best_solution": db.solution(best_prog.id) if best_prog else None,
    }


def best_details(db: Database, best, root, t0: float) -> dict:
    """The path from the initial program to the best one, and what changed."""
    if best is None:
        return {"best_lineage": [], "best_diff": ""}
    steps = [{"minutes": round((p.created - t0) / 60, 2), "score": p.score,
              "model": p.model, "mode": p.mode, "status": p.status,
              "idea": " ".join(p.idea.split())[:400]}
             for p in db.lineage(best.id, limit=500)]
    diff = ""
    if root is not None and best.id != root.id:
        lines = list(difflib.unified_diff(root.code.splitlines(), best.code.splitlines(),
                                          "initial", "best", lineterm=""))
        if len(lines) > MAX_DIFF_LINES:
            lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more lines)"]
        diff = "\n".join(lines) + "\n"
    return {"best_lineage": steps, "best_diff": diff}


def run_dir_of(result_file: Path, runs_dir: Path, result: dict) -> Path:
    """Where ``run_bench`` kept the full run behind a result file."""
    stamp = result_file.name.split("_", 1)[0]
    return (runs_dir / stamp / slug(result["example"])
            / f"{slug(result['model'])}_{result.get('variant', 'default')}")


def add_details(results_dir: Path, runs_dir: Path, log=print) -> list[Path]:
    """Give result files from before schema 2 the best program's lineage and diff,
    from the run databases that are still there; returns the files changed."""
    changed = []
    for path in sorted(results_dir.glob("*.json")):
        r = json.loads(path.read_text())
        if "best_lineage" in r:
            continue
        db_path = run_dir_of(path, runs_dir, r) / "omega.sqlite"
        if not db_path.exists():
            log(f"  {path.name}: no run database at {db_path}")
            continue
        with Database(db_path, islands=1, features=[]) as db:
            s = summarize(db)
        r["best_lineage"], r["best_diff"] = s["best_lineage"], s["best_diff"]
        r["schema"] = max(r.get("schema", 1), 2)
        path.write_text(json.dumps(r, indent=1) + "\n")
        changed.append(path)
        log(f"  {path.name}: {len(r['best_lineage'])} steps, "
            f"{r['best_diff'].count(chr(10))} diff lines")
    return changed


def add_solutions(results_dir: Path, runs_dir: Path, examples_dir: Path,
                  log=print) -> list[Path]:
    """Give the best result of every example the drawable solutions of its initial and
    best program, for results from before schema 3: both programs are taken from the
    run database and evaluated again. Returns the files changed."""
    import shutil
    import tempfile

    from .evaluator import Evaluator
    from .report import best_run, load_results
    results = load_results(results_dir)
    changed = []
    for toml in sorted(examples_dir.glob("*/omega.toml")):
        cfg = load_config(toml)
        r = best_run(results, cfg.name)
        if r is None or (r.get("best_solution") and r.get("initial_solution")):
            continue
        path = results_dir / r["_file"]
        db_path = run_dir_of(path, runs_dir, r) / "omega.sqlite"
        if not db_path.exists():
            log(f"  {path.name}: no run database at {db_path}")
            continue
        with Database(db_path, islands=1, features=[]) as db:
            root = next((p for p in db.all_programs() if p.mode == "initial"), None)
            best = db.best()
        jobs = Path(tempfile.mkdtemp(prefix="solutions-", dir=runs_dir))
        try:
            ev = Evaluator(cfg, jobs)
            found = {}
            for key, prog in (("initial_solution", root), ("best_solution", best)):
                res = ev.evaluate(prog.code, key) if prog is not None else None
                found[key] = res.solution if res is not None and res.status == "ok" else None
                log(f"  {path.name}: {key} " + (
                    "none" if res is None else
                    f"{res.status}, score {res.score}" + ("" if found[key] else ", no solution")))
                if key == "initial_solution" and res is not None and res.status == "ok" \
                        and not found[key]:
                    break  # this evaluator reports no solution at all
        finally:
            shutil.rmtree(jobs, ignore_errors=True)
        if not any(found.values()):
            continue
        d = json.loads(path.read_text())
        d.update(found)
        d["schema"] = max(d.get("schema", 1), RESULT_SCHEMA)
        path.write_text(json.dumps(d, indent=1) + "\n")
        changed.append(path)
    return changed


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def run_bench(examples: list[Path], models: list[dict], minutes: float, out_dir: Path,
              runs_dir: Path, log=print, think: bool | None = False,
              reserve_gb: float = 6.0, measured: dict[str, float] | None = None,
              max_cost_usd: float = 1.0, wait_ram_minutes: float = 0.0) -> list[Path]:
    from .engine import Engine

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    variant = {True: "think", False: "no-think"}.get(think, "default")
    for ex in examples:
        for m in models:
            if think and "thinking" not in (m.get("capabilities") or []):
                log(f"=== skipping {m['name']}: no thinking mode")
                continue
            remote = m.get("provider", "ollama") != "ollama"
            ok, why = (True, "") if remote else wait_until_fits(
                m.get("size", 0), reserve_gb, measured_gb=(measured or {}).get(m["name"]),
                minutes=wait_ram_minutes, log=log)
            if not ok:
                # Checked before every run: other applications change free memory over hours.
                log(f"=== skipping {m['name']}: {why}")
                continue
            try:
                cfg = load_config(ex)
            except Exception as e:  # noqa: BLE001 - one broken problem must not stop the bench
                log(f"=== skipping {ex}: cannot load its configuration: {e}")
                continue
            cfg.llm.models = [ModelSpec(m["name"], provider=m.get("provider"))]
            cfg.llm.max_cost_usd = max_cost_usd if remote else 0.0
            cfg.llm.insights_model = None
            cfg.llm.think = think
            cfg.search.max_minutes = minutes
            cfg.search.iterations = 100_000
            run_dir = runs_dir / stamp / slug(cfg.name) / f"{slug(m['name'])}_{variant}"
            log(f"=== {cfg.name} with {m['name']} ({variant}) for {minutes:g} min -> {run_dir}")
            details = m.get("details") or {}
            res = BenchResult(
                schema=RESULT_SCHEMA, example=cfg.name, model=m["name"], variant=variant,
                provider=m.get("provider", "ollama"),
                model_size_gb=round(m.get("size", 0) / 1e9, 1),
                model_params=details.get("parameter_size", ""),
                started=time.strftime("%Y-%m-%dT%H:%M:%S"), minutes_budget=minutes,
                wall_minutes=0.0, omega_version=__version__,
                omega_commit=git_commit(Path(__file__).parent), machine=machine_info(),
                settings={"think": think, "islands": cfg.search.islands,
                          "llm_concurrency": cfg.llm.concurrency,
                          "eval_concurrency": cfg.evaluator.concurrency,
                          "sweep_budget": cfg.search.sweep_budget,
                          "temperature": cfg.llm.temperature})
            t0 = time.monotonic()
            eng = None
            try:
                eng = Engine(cfg, run_dir, log=lambda s: log("  " + s))
                problems = eng.llm.check(reserve_gb, measured=measured)
                if problems:
                    raise RuntimeError("; ".join(problems))
                eng.run()
                memory = {} if remote else loaded_model_memory(cfg.llm.base_url, m["name"])
                for k, v in {**summarize(eng.db), **inference_summary(eng.db.calls()),
                             **memory}.items():
                    setattr(res, k, v)
                failed = res.statuses.get("llm_error", 0)
                if failed and failed == res.attempts:
                    # Not a search result: the model never answered (overloaded, refused...).
                    reason = next((p.feedback for p in eng.db.all_programs()
                                   if p.status == "llm_error"), "")
                    res.error = f"all {failed} model requests failed: {reason}"[:300]
            except Exception as e:  # noqa: BLE001 - one failing model must not stop the bench
                res.error = f"{type(e).__name__}: {e}"[:500]
                log(f"  failed: {res.error}")
            finally:
                if eng is not None:
                    eng.close()
                if not remote:
                    # Free the memory now; Ollama alone would keep the model next to the next one.
                    unload(cfg.llm.base_url, m["name"])
            res.wall_minutes = round((time.monotonic() - t0) / 60, 1)
            path = out_dir / f"{stamp}_{slug(cfg.name)}_{slug(m['name'])}_{variant}.json"
            path.write_text(json.dumps(asdict(res), indent=1) + "\n")
            written.append(path)
            log(f"  -> {path.name}: best {res.best_score} (x{(res.improvement or 0):.2f}), "
                f"{res.llm_calls} LLM calls, {res.gen_tokens_per_s} tok/s, "
                f"{res.model_memory_gb} GB")
    return written

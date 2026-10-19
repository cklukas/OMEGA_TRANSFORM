# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Command line: ``omega check|run|status|best|lineage|show|journal|notebook``."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import load_config
from .database import Database
from .prompt import fmt_score, journal_line


def _open_db(run_dir: str) -> Database:
    path = Path(run_dir) / "omega.sqlite"
    if not path.exists():
        sys.exit(f"no run database at {path}")
    db = Database(path, islands=1, features=[])
    db.islands = int(db.meta("islands", "1") or 1)
    return db


def cmd_check(a) -> int:
    from .evaluator import Evaluator
    from .llm import Ensemble

    cfg = load_config(a.config)
    print(f"problem   {cfg.name} ({cfg.language})  {cfg.initial_path}")
    if cfg.evaluator.build:
        print(f"build     {' '.join(cfg.evaluator.build)}")
    print(f"evaluator {' '.join(cfg.evaluator.command)}  stages: "
          f"{', '.join(s.name for s in cfg.evaluator.stages)}")
    print(f"models    {', '.join(m.name for m in cfg.llm.models)} via {cfg.llm.provider} "
          f"{cfg.llm.base_url}")
    problems = Ensemble(cfg.llm).check()
    for p in problems:
        print(f"  LLM: {p}")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        res = Evaluator(cfg, Path(tmp)).evaluate(cfg.initial_path.read_text(), "check")
    print(f"initial   {res.status}  score {fmt_score(res.score)}  ({res.seconds:.2f}s, stage "
          f"{res.stage})")
    if res.metrics:
        print("metrics   " + ", ".join(f"{k}={fmt_score(v) if isinstance(v, float) else v}"
                                     for k, v in res.metrics.items()))
    if res.feedback:
        print("feedback  " + res.feedback.replace("\n", "\n          "))
    if res.status != "ok":
        return 1  # the problem itself is broken
    return 3 if problems else 0  # 3: the problem is fine, but the model is not usable now


def cmd_run(a) -> int:
    from .engine import Engine

    cfg = load_config(a.config)
    if a.iterations is not None:
        cfg.search.iterations = a.iterations
    if a.minutes is not None:
        cfg.search.max_minutes = a.minutes
    if a.model or a.openrouter:
        from .config import ModelSpec
        cfg.llm.models = [ModelSpec(m) for m in a.model or []] + \
            [ModelSpec(m, provider="openrouter") for m in a.openrouter or []]
    if a.max_cost_usd is not None:
        cfg.llm.max_cost_usd = a.max_cost_usd
    if a.eval_jobs:
        cfg.evaluator.concurrency = a.eval_jobs
    if a.llm_jobs:
        cfg.llm.concurrency = a.llm_jobs
    if a.think is not None:
        cfg.llm.think = a.think
    run_dir = Path(a.run_dir) if a.run_dir else \
        cfg.problem_dir / "runs" / time.strftime("%Y%m%d-%H%M%S")
    eng = Engine(cfg, run_dir)
    if not a.skip_check:
        problems = eng.llm.check(a.reserve_gb) if hasattr(eng.llm, "check") else []
        if problems:
            for p in problems:
                print(f"LLM: {p}", file=sys.stderr)
            return 2
    eng.run()
    print(f"run directory: {run_dir}")
    return 0


def cmd_status(a) -> int:
    db = _open_db(a.run_dir)
    counts = db.status_counts()
    total = sum(counts.values())
    print(f"problem {db.meta('problem')}  programs {total}  "
          + "  ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    best = db.best()
    root = db.get(db.meta("root_id") or "")
    if best and root:
        print(f"best #{best.id[:6]} score {fmt_score(best.score)} (initial "
              f"{fmt_score(root.score)}), generation {best.generation}, iteration {best.iteration}")
    for i in range(db.islands):
        el = db.island_elites(i)
        b = el[0] if el else None
        print(f"  island {i}: {len(el):3d} niches, best "
              f"{fmt_score(b.score) if b else '-'} #{b.id[:6] if b else ''}")
    print("leaderboard:")
    for i, p in enumerate(db.top(a.top), 1):
        print(f"  {i:2d}. {journal_line(p, idea_len=100)}")
    return 0


def cmd_stats(a) -> int:
    """How well each kind of attempt works: the data for tuning the search policy."""
    db = _open_db(a.run_dir)
    every = [p for p in db.all_programs() if p.mode != "initial"]
    progs = [p for p in every if p.status != "swept"]  # strategy tables: LLM answers only
    if not progs:
        print("no attempts yet")
        return 0
    by_id = {p.id: p for p in db.all_programs()}
    t0 = min(p.created for p in by_id.values())
    llm = [p for p in progs if p.llm_seconds and not p.mode.startswith("sweep/")]
    swept = len(every) - len(progs)
    print(f"attempts {len(progs)} (+{swept} sweep variants)   "
          f"wall {(max(p.created for p in every) - t0) / 60:.1f} min   "
          f"LLM calls {len(llm)}, mean {sum(p.llm_seconds for p in llm) / max(1, len(llm)):.0f}s")

    from .bench import inference_summary
    inf = inference_summary(db.calls())
    if inf:
        print(f"inference: load {inf['load_seconds']}s, median {inf['median_call_seconds']}s/call "
              f"(p90 {inf['p90_call_seconds']}s), prompt {inf['prompt_tokens_per_s']} tok/s, "
              f"generation {inf['gen_tokens_per_s']} tok/s, {inf['mean_gen_tokens']} tokens/answer, "
              f"{inf['stopped_calls']} stopped")

    def gain(p):
        par = by_id.get(p.parent_id or "")
        return p.score - par.score if p.ok and par is not None and par.ok else None

    for label, key in (("selection", 0), ("edit", 1), ("style", 2)):
        groups: dict[str, list] = {}
        for p in progs:
            parts = p.mode.split("/")
            if parts[0] == "sweep":
                parts = parts[1:]
            if len(parts) > key:
                groups.setdefault(parts[key], []).append(p)
        print(f"by {label}:")
        for k, ps in sorted(groups.items()):
            ok = [p for p in ps if p.ok]
            gains = [g for g in map(gain, ps) if g is not None]
            better = sum(1 for g in gains if g > 1e-12)
            print(f"  {k:8s} n={len(ps):3d}  ok {100 * len(ok) / len(ps):3.0f}%  improved "
                  f"{better:2d}  mean gain {sum(gains) / len(gains) if gains else 0:+.4g}")
    print("best score over time:")
    best = None
    for p in sorted(by_id.values(), key=lambda p: p.created):
        if p.ok and (best is None or p.score > best):
            best = p.score
            print(f"  {(p.created - t0) / 60:6.1f} min  it {p.iteration:4d}  {fmt_score(p.score)}  "
                  f"{(p.idea or '')[:70]}")
    return 0


def cmd_replay(a) -> int:
    """Re-send logged prompts with other LLM settings; report how the answers fare."""
    import tempfile

    from .config import ModelSpec
    from .evaluator import Evaluator
    from .llm import Ensemble
    from .replay import replay

    db = _open_db(a.run_dir)
    cfg_dir = db.meta("config_dir")
    if not cfg_dir:
        sys.exit("run has no recorded problem directory")
    cfg = load_config(cfg_dir)
    if a.model:
        cfg.llm.models = [ModelSpec(m) for m in a.model]
    if a.think is not None:
        cfg.llm.think = a.think
    if a.temperature is not None:
        cfg.llm.temperature = a.temperature
    llm = Ensemble(cfg.llm)
    with tempfile.TemporaryDirectory() as tmp:
        ev = Evaluator(cfg, Path(tmp)) if a.evaluate else None
        results = replay(Path(a.run_dir), db, llm, a.n, evaluator=ev, seed=a.seed, ids=a.ids)
    print(f"model {', '.join(m.name for m in cfg.llm.models)}  think={cfg.llm.think}  "
          f"temperature={cfg.llm.temperature}")
    for r in results:
        score = f" score {fmt_score(r.score)}" if r.score is not None else ""
        print(f"  #{r.program_id[:6]} {r.seconds:5.0f}s {r.chars:6d} chars  {r.outcome}{score}"
              f"  {r.detail.splitlines()[0][:80] if r.detail else ''}")
    if results:
        from collections import Counter
        c = Counter(r.outcome for r in results)
        mean_s = sum(r.seconds for r in results) / len(results)
        print(f"summary: {len(results)} prompts, mean {mean_s:.0f}s, "
              + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    return 0


def cmd_bench(a) -> int:
    """Run every example with every selected model and record comparable results."""
    from .bench import installed_models, run_bench
    from .config import LLMConfig
    from .report import write_report

    base = a.base_url or LLMConfig().base_url
    models = installed_models(base)
    if a.no_local:
        models = []
    elif a.models and a.models != ["all"]:
        wanted = set(a.models)
        models = [m for m in models if m["name"] in wanted]
        missing = wanted - {m["name"] for m in models}
        if missing:
            sys.exit(f"not installed: {', '.join(sorted(missing))}")
    if a.feasible and not Path(a.feasible).exists():
        print(f"warning: {a.feasible} not found; not filtering by the model check")
    elif a.feasible:
        from .modelcheck import feasible_names
        ok = feasible_names(a.feasible)
        dropped = [m["name"] for m in models if m["name"] not in ok]
        models = [m for m in models if m["name"] in ok]
        if dropped:
            print(f"skipping models not marked ok in {a.feasible}: {', '.join(dropped)}")
    skipped = [m["name"] for m in models if m.get("size", 0) / 1e9 > a.max_size_gb]
    models = [m for m in models if m.get("size", 0) / 1e9 <= a.max_size_gb]
    models = [m for m in models if m["name"] not in set(a.exclude or [])]
    if skipped:
        print(f"skipping models above {a.max_size_gb:g} GB: {', '.join(skipped)}")
    if a.think:
        no_think = [m["name"] for m in models if "thinking" not in (m.get("capabilities") or [])]
        models = [m for m in models if m["name"] not in no_think]
        if no_think:
            print(f"skipping models without a thinking mode: {', '.join(no_think)}")
    measured = {}
    if a.feasible and Path(a.feasible).exists():
        from .modelcheck import measured_memory
        measured = measured_memory(a.feasible)
    if a.openrouter:
        from .bench import openrouter_models
        models += openrouter_models(a.openrouter)
        print(f"OpenRouter models (prompts, including program code, leave this machine; at most "
              f"${a.max_cost_usd:g} per run): {', '.join(a.openrouter)}")
    examples = [Path(e) for e in a.examples]
    hours = len(models) * len(examples) * a.minutes / 60
    print(f"{len(models)} models x {len(examples)} examples x {a.minutes:g} min = about "
          f"{hours:.1f} h:\n  " + "\n  ".join(m["name"] for m in models))
    if a.dry_run:
        return 0
    out = Path(a.out)

    def log(line: str) -> None:
        print(line, flush=True)

    for ex in examples:
        for m in models:
            run_bench([ex], [m], a.minutes, out, Path(a.runs), log=log, think=a.think,
                      reserve_gb=a.reserve_gb, measured=measured, max_cost_usd=a.max_cost_usd,
                      wait_ram_minutes=a.wait_ram_minutes)
            if a.report:
                write_report(out, Path(a.report))
    return 0


def cmd_models(a) -> int:
    """Quick feasibility check of the installed models."""
    from .bench import installed_models
    from .config import LLMConfig
    from .modelcheck import ModelCheck, check_models, keep_tested, save

    base = (a.base_url or LLMConfig().base_url).rstrip("/")
    models = installed_models(base)
    if a.models:
        models = [m for m in models if m["name"] in set(a.models)]
    if a.openrouter:
        from .bench import openrouter_models
        models += openrouter_models(a.openrouter)
    big = [m for m in models if m.get("size", 0) / 1e9 > a.max_size_gb]
    small = [m for m in models if m not in big]
    checks = check_models(small, base, timeout=a.timeout, min_tokens_per_s=a.min_tokens_per_s,
                          reserve_gb=a.reserve_gb, wait_ram_minutes=a.wait_ram_minutes)
    for m in big:
        checks.append(ModelCheck(m["name"], (m.get("details") or {}).get("parameter_size", ""),
                                 round(m.get("size", 0) / 1e9, 1),
                                 "thinking" in (m.get("capabilities") or []),
                                 verdict="too large",
                                 reason=f"weights above {a.max_size_gb:g} GB; not loaded"))
    print(f"\n{'model':38s} {'verdict':14s} {'load s':>6s} {'tok/s':>6s} {'RAM GB':>6s} "
          f"hello edit think")
    for c in checks:
        def yn(v):
            return "-" if v is None else ("yes" if v else "no")
        print(f"{c.model:38s} {c.verdict:14s} {c.load_seconds or '-':>6} "
              f"{c.gen_tokens_per_s or '-':>6} {c.memory_gb or '-':>6} {yn(c.hello_ok):5s} "
              f"{yn(c.edit_ok):4s} {yn(c.think_ok)}")
    if a.out:
        checks, kept = keep_tested(checks, a.out)
        if kept:
            print(f"\nkept the earlier results of {len(kept)} model(s) that did not fit in "
                  f"memory now: {', '.join(kept)}")
        save(checks, a.out)
        print(f"\nwrote {a.out}; {sum(c.feasible for c in checks)} of {len(checks)} models "
              f"are feasible")
    return 0


def cmd_sandbox(a) -> int:
    from .evaluator import sandbox_status
    print(sandbox_status())
    return 0


def cmd_bench_details(a) -> int:
    """Add the best program's lineage and diff to older benchmark result files."""
    from .bench import add_details
    changed = add_details(Path(a.results), Path(a.runs))
    print(f"updated {len(changed)} result file(s); regenerate the pages with: omega docs")
    return 0


def cmd_bench_solutions(a) -> int:
    """Add the initial and best program's drawable solutions to older result files."""
    from .bench import add_solutions
    changed = add_solutions(Path(a.results), Path(a.runs), Path(a.examples))
    print(f"updated {len(changed)} result file(s); regenerate the pages with: omega docs")
    return 0


def cmd_model_releases(a) -> int:
    """Fill in the release dates of the benchmarked models."""
    from .releases import load, refresh, save
    from .report import load_results
    results = load_results(Path(a.results))
    cloud = sorted({r["model"] for r in results if r.get("provider", "ollama") != "ollama"})
    models = refresh(load(Path(a.file)), cloud)
    save(models, Path(a.file))
    dated = sum(1 for m in models.values() if m.get("released"))
    local = {r["model"] for r in results if r.get("provider", "ollama") == "ollama"}
    missing = sorted(local - set(models))
    print(f"wrote {a.file}: {dated} of {len(models)} models dated")
    if missing:
        print("local models without a repository entry: " + ", ".join(missing))
    return 0


def cmd_report(a) -> int:
    from .report import write_report
    path = write_report(Path(a.results), Path(a.output))
    print(f"wrote {path}")
    from .landscape import latest_snapshot, render
    snap = latest_snapshot(Path(a.openrouter_snapshots))
    if snap is not None:
        from .report import load_results
        benchmarked = {r["model"] for r in load_results(Path(a.results))}
        page = Path(a.output).parent / "openrouter-models.md"
        page.write_text(render(snap, benchmarked))
        print(f"wrote {page}")
    return 0


def cmd_openrouter_snapshot(a) -> int:
    """Record OpenRouter's community usage rankings with current prices."""
    from .landscape import SnapshotError, save_snapshot, take_snapshot
    try:
        snap = take_snapshot()
    except (SnapshotError, OSError, ValueError) as e:
        sys.exit(f"snapshot failed: {e}")
    path = save_snapshot(snap, Path(a.out))
    print(f"wrote {path}: {len(snap['models'])} models; run `omega report` to update the page")
    return 0


def cmd_best(a) -> int:
    db = _open_db(a.run_dir)
    best = db.best()
    if best is None:
        sys.exit("no successful program yet")
    if a.output:
        Path(a.output).write_text(best.code)
        print(f"wrote #{best.id} (score {fmt_score(best.score)}) to {a.output}")
    else:
        sys.stdout.write(best.code)
    return 0


def _resolve(db: Database, pid: str | None):
    if not pid or pid == "best":
        return db.best()
    p = db.find(pid.lstrip("#"))
    if p is None:
        sys.exit(f"no unique program matches {pid!r}")
    return p


def cmd_lineage(a) -> int:
    db = _open_db(a.run_dir)
    p = _resolve(db, a.id)
    prev = None
    for q in db.lineage(p.id, limit=1000):
        print(journal_line(q, prev, idea_len=160))
        prev = q.score if q.ok else prev
    return 0


def cmd_show(a) -> int:
    db = _open_db(a.run_dir)
    p = _resolve(db, a.id)
    print(f"#{p.id} parent #{p.parent_id or '-'} island {p.island} gen {p.generation} "
          f"iteration {p.iteration} mode {p.mode} model {p.model}")
    print(f"status {p.status} score {fmt_score(p.score)} stage {p.stage} "
          f"(llm {p.llm_seconds:.1f}s, eval {p.eval_seconds:.1f}s)")
    print(f"idea: {p.idea}")
    if p.metrics:
        print(f"metrics: {p.metrics}")
    if p.feedback:
        print(f"feedback:\n{p.feedback}")
    print(p.diff if (p.diff and not a.code) else p.code)
    return 0


def cmd_journal(a) -> int:
    db = _open_db(a.run_dir)
    for p in db.recent(a.island, a.n)[::-1]:
        par = db.get(p.parent_id) if p.parent_id else None
        print(f"i{p.island} " + journal_line(p, par.score if par and par.ok else None, 160))
    return 0


def cmd_notebook(a) -> int:
    db = _open_db(a.run_dir)
    print(db.latest_note() or "(no notebook yet)")
    return 0


def cmd_docs(a) -> int:
    from .docgen import pages, write_pages
    root = Path(__file__).resolve().parent.parent
    if a.check:
        stale = [rel for rel, text in pages().items()
                 if not (root / rel).exists() or (root / rel).read_text() != text]
        for rel in stale:
            print(f"out of date: {rel} (run: omega docs)")
        return 1 if stale else 0
    for p in write_pages(root):
        print(f"wrote {p.relative_to(root)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="omega", description="OMEGA TRANSFORM: LLM-driven "
                                 "evolutionary program optimization with local models")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate a problem: evaluate the initial program, check "
                       "the model (exit 1: the problem is broken; 3: only the model is unusable)")
    c.add_argument("config", help="omega.toml or the problem directory")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("run", help="start or resume a search")
    r.add_argument("config", help="omega.toml or the problem directory")
    r.add_argument("--run-dir", help="run directory (reuse one to resume; default: runs/<time> in the problem directory)")
    r.add_argument("-n", "--iterations", type=int, help="model answers to run (overrides search.iterations)")
    r.add_argument("--minutes", type=float, help="time limit (overrides search.max_minutes)")
    r.add_argument("-m", "--model", action="append", help="override models (repeatable)")
    r.add_argument("--openrouter", action="append", metavar="MODEL",
                   help="use this OpenRouter model (cloud; repeatable, combines with -m)")
    r.add_argument("--max-cost-usd", type=float,
                   help="stop once the run's model requests cost this much (llm.max_cost_usd)")
    r.add_argument("-j", "--eval-jobs", type=int, help="parallel evaluation jobs")
    r.add_argument("--llm-jobs", type=int, help="parallel LLM requests")
    r.add_argument("--think", action=argparse.BooleanOptionalAction, default=None,
                   help="enable/disable model reasoning (ollama think flag)")
    r.add_argument("--reserve-gb", type=float, default=6.0,
                   help="RAM to keep free; a model that would not fit is not loaded")
    r.add_argument("--skip-check", action="store_true",
                   help="do not verify the model first (installed, fits in memory)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="summary of a run")
    s.add_argument("run_dir", help="run directory")
    s.add_argument("--top", type=int, default=10, help="leaderboard length")
    s.set_defaults(fn=cmd_status)

    st = sub.add_parser("stats", help="success rates by strategy, LLM time, best over time")
    st.add_argument("run_dir", help="run directory")
    st.set_defaults(fn=cmd_stats)

    rp = sub.add_parser("replay", help="re-send logged prompts with other LLM settings")
    rp.add_argument("run_dir", help="run directory")
    rp.add_argument("-n", type=int, default=6, help="number of most recent prompts")
    rp.add_argument("--ids", nargs="+", help="specific program ids instead")
    rp.add_argument("-m", "--model", action="append", help="model to use (repeatable)")
    rp.add_argument("--think", action=argparse.BooleanOptionalAction, default=None,
                    help="enable/disable model reasoning")
    rp.add_argument("--temperature", type=float, help="sampling temperature")
    rp.add_argument("--evaluate", action="store_true", help="also evaluate applicable answers")
    rp.add_argument("--seed", type=int, default=0, help="random seed for model choice")
    rp.set_defaults(fn=cmd_replay)

    bn = sub.add_parser("bench", help="compare Ollama models on the examples (long-running)")
    bn.add_argument("--examples", nargs="+", help="problem directories",
                    default=["examples/circle_packing", "examples/matmul_st_cpp"])
    bn.add_argument("--models", nargs="+", default=["all"], help="model names or 'all'")
    bn.add_argument("--exclude", nargs="+", help="model names to leave out")
    bn.add_argument("--max-size-gb", type=float, default=22.0,
                    help="skip models whose weights exceed this size (default 22)")
    bn.add_argument("--minutes", type=float, default=15.0, help="search budget per run")
    bn.add_argument("--think", action=argparse.BooleanOptionalAction, default=False,
                    help="enable/disable model reasoning")
    bn.add_argument("--out", default="bench/results", help="result JSON directory (commit it)")
    bn.add_argument("--runs", default="bench/runs", help="full run directories (local)")
    bn.add_argument("--report", default="docs/benchmarks.md", help="page regenerated after each run")
    bn.add_argument("--base-url", help="Ollama server (default http://localhost:11434)")
    bn.add_argument("--wait-ram-minutes", type=float, default=0.0,
                    help="wait up to this long for memory before each model instead of "
                         "skipping it (other applications free memory over time)")
    bn.add_argument("--reserve-gb", type=float, default=6.0,
                    help="RAM to keep free; checked before every run, models that would not "
                         "fit are skipped")
    bn.add_argument("--openrouter", nargs="+", metavar="MODEL",
                    help="also benchmark these OpenRouter models (cloud; needs a key)")
    bn.add_argument("--no-local", action="store_true",
                    help="no local models: only the --openrouter models")
    bn.add_argument("--max-cost-usd", type=float, default=1.0,
                    help="stop an OpenRouter run once its requests cost this much")
    bn.add_argument("--feasible", metavar="FILE",
                    help="only models marked ok in this `omega models` result file")
    bn.add_argument("--dry-run", action="store_true", help="only list what would run")
    bn.set_defaults(fn=cmd_bench)

    bd = sub.add_parser("bench-details", help="add the best program's lineage and diff to "
                        "benchmark result files written before they were recorded")
    bd.add_argument("--results", default="bench/results", help="result JSON directory")
    bd.add_argument("--runs", default="bench/runs", help="the run directories behind them")
    bd.set_defaults(fn=cmd_bench_details)

    bs = sub.add_parser("bench-solutions", help="add the drawable solutions of the initial "
                        "and best program to each example's best benchmark result, by "
                        "evaluating both again (for results written before they were recorded)")
    bs.add_argument("--results", default="bench/results", help="result JSON directory")
    bs.add_argument("--runs", default="bench/runs", help="the run directories behind them")
    bs.add_argument("--examples", default="examples", help="the example problems")
    bs.set_defaults(fn=cmd_bench_solutions)

    mc = sub.add_parser("models", help="quick check whether installed models load and work")
    mc.add_argument("--models", nargs="+", help="only these models (default: all installed)")
    mc.add_argument("--max-size-gb", type=float, default=22.0,
                    help="do not load models whose weights exceed this size")
    mc.add_argument("--wait-ram-minutes", type=float, default=0.0,
                    help="wait up to this long for memory before each model instead of "
                         "skipping it (other applications free memory over time)")
    mc.add_argument("--reserve-gb", type=float, default=6.0,
                    help="RAM to keep free for the system, evaluation jobs and other "
                         "applications; models that would not fit are not loaded")
    mc.add_argument("--min-tokens-per-s", type=float, default=8.0,
                    help="generation speed below which a model is marked slow")
    mc.add_argument("--timeout", type=float, default=900.0,
                    help="seconds to wait for a model to load and answer (first loads from a "
                         "slow disk, or with a format migration, can take minutes)")
    mc.add_argument("--openrouter", nargs="+", metavar="MODEL",
                    help="also check these OpenRouter models (cloud; a few small paid requests)")
    mc.add_argument("--out", default="bench/model-check.json",
                    help="result file read by `bench --feasible`")
    mc.add_argument("--base-url", help="Ollama server (default http://localhost:11434)")
    mc.set_defaults(fn=cmd_models)

    sb = sub.add_parser("sandbox", help="show which sandbox jobs run in on this machine")
    sb.set_defaults(fn=cmd_sandbox)

    rpt = sub.add_parser("report", help="render bench results into the docs page")
    rpt.add_argument("--results", default="bench/results", help="result JSON directory")
    rpt.add_argument("-o", "--output", default="docs/benchmarks.md", help="page to write")
    rpt.add_argument("--openrouter-snapshots", default="bench/openrouter",
                     help="OpenRouter snapshots; the newest becomes docs/openrouter-models.md")
    rpt.set_defaults(fn=cmd_report)

    mr = sub.add_parser("model-releases", help="fill in when each benchmarked model was released "
                        "(Hugging Face and OpenRouter; for the timeline per developer)")
    mr.add_argument("--file", default="bench/model-releases.json",
                    help="release dates; local models need their Hugging Face repository there")
    mr.add_argument("--results", default="bench/results", help="result JSON directory")
    mr.set_defaults(fn=cmd_model_releases)

    osn = sub.add_parser("openrouter-snapshot",
                         help="record OpenRouter's most used models with their prices")
    osn.add_argument("--out", default="bench/openrouter", help="snapshot directory (commit it)")
    osn.set_defaults(fn=cmd_openrouter_snapshot)

    b = sub.add_parser("best", help="print or save the best program")
    b.add_argument("run_dir", help="run directory")
    b.add_argument("-o", "--output", help="write to this file instead of standard output")
    b.set_defaults(fn=cmd_best)

    ln = sub.add_parser("lineage", help="the chain of ideas that led to a program")
    ln.add_argument("run_dir", help="run directory")
    ln.add_argument("id", nargs="?", default="best", help="program id (default: the best)")
    ln.set_defaults(fn=cmd_lineage)

    sh = sub.add_parser("show", help="details and diff of one program")
    sh.add_argument("run_dir", help="run directory")
    sh.add_argument("id", nargs="?", default="best", help="program id (default: the best)")
    sh.add_argument("--code", action="store_true", help="full code instead of the diff")
    sh.set_defaults(fn=cmd_show)

    j = sub.add_parser("journal", help="recent attempts")
    j.add_argument("run_dir", help="run directory")
    j.add_argument("-n", type=int, default=40, help="number of attempts")
    j.add_argument("--island", type=int, help="only this island")
    j.set_defaults(fn=cmd_journal)

    nb = sub.add_parser("notebook", help="the current lessons notebook")
    nb.add_argument("run_dir", help="run directory")
    nb.set_defaults(fn=cmd_notebook)

    dc = sub.add_parser("docs", help="regenerate the reference pages from the code")
    dc.add_argument("--check", action="store_true",
                    help="only report whether the committed pages are current (exit 1 if not)")
    dc.set_defaults(fn=cmd_docs)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())

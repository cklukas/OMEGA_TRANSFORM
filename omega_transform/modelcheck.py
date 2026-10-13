# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Quick feasibility check of installed models before a long benchmark.

Each model gets three short requests: a greeting (does it load and answer
sensibly?), a three-line edit in the engine's SEARCH/REPLACE format (can its
answers be applied at all?), and, if the model advertises one, the same
greeting with its reasoning mode on. Speed and memory come from what Ollama
reports. The model is unloaded afterwards, so the next one starts with free
memory and its load time is measured cold.
"""
from __future__ import annotations

import json
import random
import time
import urllib.request
from dataclasses import asdict, dataclass

from .blocks import EditError, apply_answer, parse_response
from .config import LLMConfig, ModelSpec
from .llm import Ensemble, LLMError
from .memory import DEFAULT_RESERVE_GB, available_gb, wait_until_fits
from .prompt import DIFF_FORMAT

EDIT_PROGRAM = """# OMEGA-BLOCK-START
def answer():
    return 1
# OMEGA-BLOCK-END
"""
EDIT_PROMPT = (
    "Change the program so that answer() returns 42.\n\n"
    f"```python\n{EDIT_PROGRAM}```\n\n{DIFF_FORMAT}")


@dataclass
class ModelCheck:
    model: str
    params: str
    size_gb: float
    thinking_capable: bool
    #: ok | slow | broken | failed to load | not enough RAM | too large | rate limited |
    #: unavailable (cloud model refused for this client)
    verdict: str = ""
    reason: str = ""
    load_seconds: float | None = None
    gen_tokens_per_s: float | None = None
    prompt_tokens_per_s: float | None = None
    memory_gb: float | None = None
    hello: str = ""
    hello_ok: bool = False
    edit_ok: bool = False
    edit_detail: str = ""
    think_ok: bool | None = None
    seconds: float = 0.0

    @property
    def feasible(self) -> bool:
        return self.verdict == "ok"


def unload(base: str, name: str, settle_seconds: float = 20.0) -> None:
    """Drop a model from memory and wait until the memory is really free again.

    Ollama answers the unload request before its server process has exited, and
    the system reclaims the pages some seconds later still; measuring free memory
    for the next model before then would see the old model as still in use.
    """
    body = json.dumps({"model": name, "keep_alive": 0}).encode()
    req = urllib.request.Request(f"{base}/api/generate", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except OSError:
        return
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and _memory(base, name) is not None:
        time.sleep(1)
    # Wait until free memory stops growing (pages returned to the system).
    last = available_gb() or 0.0
    end = time.monotonic() + settle_seconds
    while time.monotonic() < end:
        time.sleep(2)
        now = available_gb() or 0.0
        if now - last < 0.2:
            break
        last = now


def _memory(base: str, name: str) -> float | None:
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=10) as r:
            for m in json.loads(r.read()).get("models", []):
                if name in (m.get("name"), m.get("model")):
                    return round(m.get("size", 0) / 1e9, 1)
    except (OSError, ValueError):
        pass
    return None


def check_model(m: dict, base: str, num_ctx: int = 32768, timeout: float = 900.0,
                min_tokens_per_s: float = 8.0, reserve_gb: float = DEFAULT_RESERVE_GB,
                wait_ram_minutes: float = 0.0, log=None) -> ModelCheck:
    details = m.get("details") or {}
    res = ModelCheck(model=m["name"], params=details.get("parameter_size", ""),
                     size_gb=round(m.get("size", 0) / 1e9, 1),
                     thinking_capable="thinking" in (m.get("capabilities") or []))
    remote = m.get("provider", "ollama") != "ollama"
    ok, why = (True, "") if remote else wait_until_fits(
        m.get("size", 0), reserve_gb, minutes=wait_ram_minutes, log=log)
    if not ok:
        res.verdict, res.reason = "not enough RAM", why + "; not loaded"
        return res
    t0 = time.monotonic()
    cfg = LLMConfig(base_url=base, models=[ModelSpec(m["name"], provider=m.get("provider"))],
                    num_ctx=num_ctx,
                    timeout=timeout, temperature=0.2, max_tokens=1024, think=False,
                    keep_alive="5m")
    llm = Ensemble(cfg)
    rng = random.Random(0)
    try:
        # 1. Load and greet.
        try:
            text, _ = llm.complete([{"role": "user", "content":
                                     "Reply with exactly one word: hello"}], rng, max_chars=2000)
        except LLMError as e:
            msg = str(e)
            if "rate limited" in msg or "429" in msg:
                res.verdict = "rate limited"
            elif remote:
                res.verdict = "unavailable"
            else:
                res.verdict = "failed to load"
            res.reason = msg[:200]
            return res
        st = llm.last_stats
        res.load_seconds = round(st.load_seconds, 1)
        res.hello = text.strip()[:60]
        res.hello_ok = "hello" in text.lower() and len(text.strip()) < 200
        res.memory_gb = None if remote else _memory(base, m["name"])

        # 2. The edit format, the thing every search iteration depends on.
        try:
            text, _ = llm.complete([{"role": "user", "content": EDIT_PROMPT}], rng,
                                   max_chars=6000)
            st = llm.last_stats
            if st.gen_seconds > 0:
                res.gen_tokens_per_s = round(st.gen_tokens / st.gen_seconds, 1)
            if st.prompt_seconds > 0:
                res.prompt_tokens_per_s = round(st.prompt_tokens / st.prompt_seconds, 1)
            new = apply_answer(EDIT_PROGRAM, parse_response(text))
            res.edit_ok = "42" in new
            res.edit_detail = "applied" if res.edit_ok else "applied, but no 42"
        except (LLMError, EditError) as e:
            res.edit_detail = str(e)[:160]

        # 3. Reasoning mode, if advertised.
        if res.thinking_capable:
            cfg.think = True
            try:
                text, _ = llm.complete([{"role": "user", "content":
                                         "Reply with exactly one word: hello"}], rng,
                                       max_chars=2000)
                res.think_ok = "hello" in text.lower()
            except LLMError:
                res.think_ok = False

        if not res.hello_ok and not res.edit_ok:
            res.verdict, res.reason = "broken", f"unexpected answers, e.g. {res.hello!r}"
        elif res.gen_tokens_per_s is not None and res.gen_tokens_per_s < min_tokens_per_s:
            res.verdict = "slow"
            res.reason = f"{res.gen_tokens_per_s} tokens/s < {min_tokens_per_s:g}"
        else:
            res.verdict = "ok"
            if not res.edit_ok:
                res.reason = "edit format not followed (benchmarked anyway)"
        return res
    finally:
        res.seconds = round(time.monotonic() - t0, 1)
        if not remote:
            unload(base, m["name"])


def check_models(models: list[dict], base: str, log=print, **kw) -> list[ModelCheck]:
    out = []
    for m in models:
        log(f"checking {m['name']} ({round(m.get('size', 0) / 1e9, 1)} GB) ...")
        r = check_model(m, base, log=log, **kw)
        log(f"  {r.verdict:14s} load {r.load_seconds}s, {r.gen_tokens_per_s} tok/s, "
            f"{r.memory_gb} GB, hello={r.hello_ok}, edit={r.edit_ok}, think={r.think_ok}"
            + (f"  ({r.reason})" if r.reason else ""))
        out.append(r)
    return out


def keep_tested(checks: list[ModelCheck], path) -> tuple[list[ModelCheck], list[str]]:
    """Models that could not be loaded now for lack of free memory keep the result
    of the previous check in ``path``, if that one really tested them: memory that
    other applications hold for a while says nothing about the model, and the
    benchmark reads only models marked ok. Returns the checks and the names kept."""
    from dataclasses import fields
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return checks, []
    try:
        previous = {m["model"]: m for m in json.loads(p.read_text()).get("models", [])}
    except (ValueError, KeyError):
        return checks, []
    names = {f.name for f in fields(ModelCheck)}
    out, kept = [], []
    for c in checks:
        old = previous.get(c.model)
        if c.verdict == "not enough RAM" and old and old.get("verdict") not in (
                None, "", "not enough RAM"):
            out.append(ModelCheck(**{k: v for k, v in old.items() if k in names}))
            kept.append(c.model)
        else:
            out.append(c)
    return out, kept


def save(checks: list[ModelCheck], path) -> None:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "models": [asdict(c) for c in checks]}, indent=1) + "\n")


def measured_memory(path) -> dict[str, float]:
    """Loaded size per model as measured by a model check."""
    from pathlib import Path
    data = json.loads(Path(path).read_text())
    return {m["model"]: m["memory_gb"] for m in data.get("models", []) if m.get("memory_gb")}


def feasible_names(path) -> set[str]:
    from pathlib import Path
    data = json.loads(Path(path).read_text())
    return {m["model"] for m in data.get("models", []) if m.get("verdict") == "ok"}

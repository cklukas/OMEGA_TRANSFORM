# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Replay logged prompts under different LLM settings.

Every iteration logs its exact prompt (``llm/<id>.md``). Replaying a sample of
them answers questions like "does thinking help this model?" or "is model B
better at following the edit format?" in minutes, without running a search.
Each answer is parsed and applied to the original parent program; with
``evaluate`` it is also scored by the problem's evaluator.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .blocks import EditError, apply_answer, parse_response
from .database import Database
from .llm import LLM, LLMError

_SECTION = re.compile(r"^---- (system|user|response) ----$", re.M)


def read_log(path: Path) -> tuple[list[dict[str, str]], str]:
    """(messages, response) from an ``llm/<id>.md`` log written by the engine."""
    text = path.read_text()
    parts = _SECTION.split(text)
    messages, response = [], ""
    for role, body in zip(parts[1::2], parts[2::2]):
        body = body.strip("\n")
        if role == "response":
            response = body
        else:
            messages.append({"role": role, "content": body})
    return messages, response


@dataclass
class ReplayResult:
    program_id: str
    seconds: float
    chars: int
    outcome: str  # applies | edit_error | llm_error | ok/invalid/... (with evaluate)
    detail: str = ""
    score: float | None = None


def replay(run_dir: Path, db: Database, llm: LLM, n: int, max_chars: int = 0,
           evaluator=None, seed: int = 0, ids: list[str] | None = None) -> list[ReplayResult]:
    logs = sorted((run_dir / "llm").glob("*.md"), key=lambda p: p.stat().st_mtime)
    if ids:
        logs = [p for p in logs if any(p.stem.startswith(i) for i in ids)]
    else:
        logs = logs[-n:]
    rng = random.Random(seed)
    out = []
    for path in logs:
        prog = db.get(path.stem)
        parent = db.get(prog.parent_id) if prog and prog.parent_id else None
        if parent is None:
            continue
        messages, _ = read_log(path)
        budget = max_chars or 2 * len(parent.code) + 8000
        t0 = time.monotonic()
        try:
            text, _ = llm.complete(messages, rng, max_chars=budget)
        except LLMError as e:
            out.append(ReplayResult(path.stem, time.monotonic() - t0, len(e.partial),
                                    "llm_error", str(e)))
            continue
        secs = time.monotonic() - t0
        try:
            code = apply_answer(parent.code, parse_response(text))
        except EditError as e:
            out.append(ReplayResult(path.stem, secs, len(text), "edit_error", str(e)))
            continue
        res = ReplayResult(path.stem, secs, len(text), "applies")
        if evaluator is not None:
            ev = evaluator.evaluate(code, f"replay-{path.stem}")
            res.outcome, res.score, res.detail = ev.status, ev.score, ev.feedback
        out.append(res)
    return out

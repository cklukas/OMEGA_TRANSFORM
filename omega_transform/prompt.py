# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Prompt construction: turn the search history into a compact research brief.

A local model has a small context window and little patience for noise, so
the history is condensed into one-line journal entries instead of code:

  #a1b2 g4 0.8123 (+0.0120) [exploit/diff] IDEA: hexagonal seed layout
  #c3d4 g5 FAILED compile_error [explore/diff] IDEA: SIMD inner loop | error: ...

The prompt sections, in order:
  task, scoring, lessons notebook, leaderboard, lineage of the parent,
  attempts already made from the parent, other recent attempts on the
  island, inspiration programs (editable region only), the parent itself in
  full, then the instruction for this attempt and the answer format.
Full code appears only for the parent and the inspirations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .blocks import editable_text
from .config import ProblemConfig
from .database import Program

SYSTEM = (
    "You are an expert {lang} performance engineer and algorithm researcher working "
    "inside an automated evolutionary search. Each answer you give is applied to a "
    "program, compiled or run, and scored by an evaluator. Make one well-reasoned "
    "change per answer, follow the answer format exactly, and never change code "
    "outside the OMEGA-BLOCK regions. The program is a single file: everything you need "
    "(helpers, lookup tables, constants) must be written inside the OMEGA-BLOCK regions; "
    "you cannot add files, headers or modules. Decide before you write: code comments "
    "must be short and describe the code, never your deliberation."
)

DIFF_FORMAT = """\
Answer format:
IDEA: <one sentence: the single change you make and why it should raise the score>
Then one or more edit blocks, exactly like this:
<<<<<<< SEARCH
<lines copied exactly from the current program, indentation included>
=======
<the new lines>
>>>>>>> REPLACE
Rules: each SEARCH must match the current program exactly and lie inside an
OMEGA-BLOCK region; keep SEARCH sections short but unique; edits apply in order.
Do not add explanations after the edit blocks."""

REWRITE_FORMAT = """\
Answer format:
IDEA: <one sentence: the approach you implement and why it should raise the score>
Then one fenced ```{lang} code block containing the complete new content of the
OMEGA-BLOCK region (without the marker lines). It must keep every function
signature that the code outside the region relies on. No explanations after it."""

SWEEP_FORMAT = """\
Optional SWEEP: if your change depends on numeric constants (or a choice between a
few variants) whose best value is uncertain, do not guess. Define each as a named
constant inside the OMEGA-BLOCK region ({decl}) and end your answer (after the
edits) with:
SWEEP:
NAME = [v1, v2, v3]          explicit values (numbers or strings)
NAME = range(lo, hi)         continuous, uniform
NAME = logrange(lo, hi)      continuous, log-uniform (lo > 0)
NAME = intrange(lo, hi)      integers
At most 4 parameters; values must be plain literals (no expressions). A SWEEP never
replaces edits: every swept constant must exist in the program after your edits.
The engine then evaluates up to {budget} variants for you and keeps the best one,
so put plausible values in the code and wide ranges in SWEEP."""

SWEEP_DECL = {
    "python": "Python: `NAME = 0.5` at the start of a line",
    "cpp": "C++: `constexpr double NAME = 0.5;` or `#define NAME 0.5`",
    "c": "C: `#define NAME 0.5` or `static const double NAME = 0.5;`",
}

STYLES = {
    "refine": "Make a focused, incremental improvement to the current program: tune, "
              "optimize, or fix a weakness that the metrics or feedback reveal.",
    "bold": "Try a substantially different idea from those in the lineage and the "
            "leaderboard: a different algorithm, data layout, or construction. Avoid "
            "approaches that already failed.",
    "fix": "The current program FAILED (see its error). Keep its idea, but fix the "
           "bug so it runs correctly and scores well.",
    "tune": "Look for numeric parameters or alternative variants in the current program "
            "whose values are probably not optimal, expose them as named constants, and "
            "let the engine find good values with a SWEEP (see below).",
}

STATUS_SHORT = {
    "compile_error": "compile error", "runtime_error": "crashed", "timeout": "timed out",
    "invalid": "invalid result", "filtered": "failed cascade gate",
    "edit_error": "edit not applicable", "duplicate": "duplicate of earlier program",
    "llm_error": "no usable answer",
}


_PY_ERR = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Overflow))\b")
_PY_FUNC = re.compile(r'line \d+, in (\w+)')
_C_ERR = re.compile(r"error: ([^\n|]{1,80})")


def error_signature(p: Program) -> str:
    """A short, comparable description of why an attempt failed, or "" if none."""
    if p.ok or not p.feedback or p.status in ("duplicate", "llm_error"):
        return ""
    fb = p.feedback
    if p.status == "compile_error":
        m = _C_ERR.search(fb)
        if m:  # strip identifiers in quotes so the same mistake on another name groups
            return "compile error: " + re.sub(r"'[^']*'", "'…'", m.group(1)).strip()
    m = _PY_ERR.search(fb)
    if m:
        if m.group(1) in ("SyntaxError", "IndentationError"):
            return m.group(1)  # where it happened says nothing: the code did not load
        funcs = [f for f in _PY_FUNC.findall(fb)
                 if not f.startswith("_") and f not in ("main", "run")]  # skip internals
        return m.group(1) + (f" in {funcs[-1]}()" if funcs else "")
    if p.status in ("timeout", "edit_error"):
        return {"timeout": "timeout: the program ran too long",
                "edit_error": "edit could not be applied"}[p.status]
    return ""


def recurring_errors(attempts: list[Program], min_count: int = 2) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for p in attempts:
        sig = error_signature(p)
        if sig:
            counts[sig] = counts.get(sig, 0) + 1
    return sorted(((s, n) for s, n in counts.items() if n >= min_count), key=lambda t: -t[1])


@dataclass
class Context:
    parent: Program
    selection: str  # exploit | explore | best | repair
    edit: str  # diff | rewrite
    style: str  # refine | bold | fix
    island: int
    lineage: list[Program] = field(default_factory=list)
    children: list[Program] = field(default_factory=list)
    recent: list[Program] = field(default_factory=list)
    errors: list[tuple[str, int]] = field(default_factory=list)  # recurring failure causes
    leaders: list[Program] = field(default_factory=list)
    inspirations: list[Program] = field(default_factory=list)
    notes: str = ""
    lineage_scores: dict[str, float | None] = field(default_factory=dict)


def fmt_score(s: float | None) -> str:
    if s is None:
        return "-"
    a = abs(s)
    if a != 0 and (a >= 1e5 or a < 1e-3):
        return f"{s:.4g}"
    return f"{s:.5f}".rstrip("0").rstrip(".") if a < 10 else f"{s:.4f}"


def _one_line(text: str, n: int) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def journal_line(p: Program, parent_score: float | None = None, idea_len: int = 140) -> str:
    sel = p.mode.split("/")[0] if p.mode else ""
    head = f"#{p.id[:6]} g{p.generation}"
    if p.ok:
        delta = ""
        if parent_score is not None and p.score is not None:
            delta = f" ({p.score - parent_score:+.4g})"
        head += f" score {fmt_score(p.score)}{delta}"
    else:
        head += f" FAILED: {STATUS_SHORT.get(p.status, p.status)}"
    if sel:
        head += f" [{sel}]"
    idea = _one_line(p.idea, idea_len) if p.idea else "(no idea stated)"
    line = f"{head} IDEA: {idea}"
    if not p.ok and p.feedback:
        line += f" | {_one_line(p.feedback, 160)}"
    elif p.feedback:
        sweep = next((ln for ln in p.feedback.splitlines() if ln.startswith("sweep of")), "")
        if sweep:
            line += f" | {_one_line(sweep, 260)}"
    return line


def metrics_line(p: Program, limit: int = 12) -> str:
    items = list(p.metrics.items())[:limit]
    if not items:
        return ""
    return ", ".join(f"{k}={fmt_score(v) if isinstance(v, (int, float)) else v}" for k, v in items)


def build_messages(cfg: ProblemConfig, ctx: Context, char_budget: int) -> list[dict[str, str]]:
    lang = cfg.lang_tag
    sections: list[tuple[str, str, bool]] = []  # (title, body, droppable)

    task = cfg.description.strip() or f"Improve the program {cfg.initial}."
    if cfg.hints.strip():
        task += "\n\nExpert hints:\n" + cfg.hints.strip()
    sections.append(("TASK", task, False))
    scoring = "An automated evaluator scores each program; HIGHER score is better."
    if cfg.metrics_help.strip():
        scoring += "\n" + cfg.metrics_help.strip()
    sections.append(("SCORING", scoring, False))

    for name in cfg.context_files:
        try:
            text = (cfg.problem_dir / name).read_text(encoding="utf-8")
        except OSError:
            continue
        if len(text) > 8000:
            text = text[:8000] + "\n... (truncated)"
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        sections.append((f"FIXED CODE {name} (read-only: compiled separately and linked with "
                         "your program; it calls your code, you cannot change it)",
                         f"```{ext}\n{text.rstrip()}\n```", False))

    if ctx.notes:
        sections.append(("LESSONS LEARNED SO FAR (from earlier attempts)", ctx.notes.strip(), True))

    if ctx.leaders:
        lines = [f"{i}. {journal_line(p, idea_len=110)}" for i, p in enumerate(ctx.leaders, 1)]
        sections.append(("LEADERBOARD (best programs found by any branch)", "\n".join(lines), True))

    if len(ctx.lineage) > 1:
        lines, prev = [], None
        for p in ctx.lineage:
            lines.append(journal_line(p, prev))
            prev = p.score if p.ok else prev
        sections.append(("LINEAGE OF THE CURRENT PROGRAM (oldest first; each line is one step)",
                         "\n".join(lines), True))

    if ctx.children:
        ps = ctx.parent.score if ctx.parent.ok else None
        lines = [journal_line(c, ps) for c in ctx.children]
        sections.append((
            "ALREADY TRIED ON THE CURRENT PROGRAM (do not repeat these; learn from them)",
            "\n".join(lines), True))

    if ctx.errors:
        lines = [f"- {sig}: {n} times" for sig, n in ctx.errors]
        sections.append((
            "RECURRING ERRORS IN RECENT ATTEMPTS (fix the cause; do not repeat these mistakes)",
            "\n".join(lines), True))

    if ctx.recent:
        lines = [journal_line(p, ctx.lineage_scores.get(p.parent_id or "")) for p in ctx.recent]
        sections.append(("OTHER RECENT ATTEMPTS IN THIS RESEARCH BRANCH", "\n".join(lines), True))

    for i, p in enumerate(ctx.inspirations, 1):
        body = (f"score {fmt_score(p.score)}; IDEA: {_one_line(p.idea, 200) or '(initial)'}\n"
                f"```{lang}\n{editable_text(p.code)}\n```")
        sections.append((f"INSPIRATION PROGRAM {i} (editable region only)", body, True))

    par = ctx.parent
    if par.ok:
        head = f"Program #{par.id[:6]}, score {fmt_score(par.score)}"
    else:
        head = f"Program #{par.id[:6]} FAILED ({STATUS_SHORT.get(par.status, par.status)})"
    if par.idea:
        head += f"\nIts last change: {_one_line(par.idea, 300)}"
    ml = metrics_line(par)
    if ml:
        head += f"\nMetrics: {ml}"
    if par.feedback:
        label = "Error output" if not par.ok else "Evaluator feedback"
        head += f"\n{label}:\n{par.feedback.strip()[:2500]}"
    sections.append(("CURRENT PROGRAM (the one you modify)",
                     f"{head}\n```{lang}\n{par.code.rstrip()}\n```", False))

    instr = STYLES[ctx.style]
    fmt = DIFF_FORMAT if ctx.edit == "diff" else REWRITE_FORMAT.format(lang=lang)
    if cfg.search.sweep_budget > 0 and ctx.style != "fix":
        fmt += "\n\n" + SWEEP_FORMAT.format(decl=SWEEP_DECL.get(lang, SWEEP_DECL["cpp"]),
                                           budget=cfg.search.sweep_budget)
    sections.append(("YOUR TASK NOW", f"{instr}\n\n{fmt}", False))

    # Shrink to budget: drop the least valuable droppable sections first.
    drop_order = ["OTHER RECENT", "INSPIRATION PROGRAM 2", "INSPIRATION PROGRAM 3",
                  "LEADERBOARD", "INSPIRATION PROGRAM 1", "LESSONS", "LINEAGE", "ALREADY"]

    def render() -> str:
        return "\n\n".join(f"## {t}\n{b}" for t, b, _ in sections)

    text = render()
    for prefix in drop_order:
        if len(text) <= char_budget:
            break
        sections = [s for s in sections if not (s[2] and s[0].startswith(prefix))]
        text = render()

    return [{"role": "system", "content": SYSTEM.format(lang=lang)},
            {"role": "user", "content": text}]


INSIGHTS_PROMPT = """\
You maintain the lab notebook of an automated search that improves a program.
TASK: {task}

Previous notebook:
{notes}

Recent attempts (newest last; score deltas are relative to each attempt's parent):
{journal}

Best score so far: {best}

Write the updated notebook: at most 12 short bullet points, most important first.
Cover: which ideas raised the score (with rough effect sizes), which ideas failed and
why (bugs, timeouts, no gain), recurring mistakes to avoid, and 2-3 concrete promising
directions not tried yet. Be specific and factual; no preamble, bullets only."""


def build_insights_messages(cfg: ProblemConfig, notes: str, journal: list[str],
                            best: float | None) -> list[dict[str, str]]:
    msg = INSIGHTS_PROMPT.format(
        task=_one_line(cfg.description or cfg.name, 600),
        notes=notes.strip() or "(empty)",
        journal="\n".join(journal),
        best=fmt_score(best))
    return [{"role": "user", "content": msg}]

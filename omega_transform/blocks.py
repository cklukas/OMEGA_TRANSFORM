# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Editable-region handling and LLM edit application.

A candidate program is a single source file. Regions the LLM may change are
fenced by marker comments (``# OMEGA-BLOCK-START`` / ``// OMEGA-BLOCK-END``).
Everything outside those regions is the fixed harness and must survive every
edit byte for byte.

The LLM answers with either SEARCH/REPLACE edit blocks (diff mode) or one
fenced code block holding the new content of the editable region (rewrite
mode). Both paths end in ``check_fixed_unchanged`` so an edit can never touch
the harness or the evaluator interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARK = r"^[ \t]*(?://|#|/\*|--)[ \t]*OMEGA-BLOCK-{kind}\b.*$"
START_RE = re.compile(_MARK.format(kind="START"))
END_RE = re.compile(_MARK.format(kind="END"))

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_IDEA_RE = re.compile(r"^[ \t>*_#-]*IDEA[*_]*[ \t]*:[ \t]*(.+)$", re.M | re.I)
_EDIT_RE = re.compile(
    r"<{5,9}[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n?={5,9}[ \t]*\r?\n(.*?)\r?\n?>{5,9}[ \t]*REPLACE",
    re.S,
)
_FENCE_RE = re.compile(r"```[\w+#.-]*[ \t]*\r?\n(.*?)```", re.S)


class EditError(Exception):
    """An LLM edit could not be applied; the message is fed back to the LLM."""


def split_segments(code: str) -> list[tuple[bool, str]]:
    """Split ``code`` into alternating (editable, text) segments.

    Marker lines belong to the fixed segments. A file without markers is one
    editable region surrounded by two empty fixed segments.
    """
    lines = code.splitlines(keepends=True)
    if not any(START_RE.match(ln) for ln in lines):
        return [(False, ""), (True, code), (False, "")]
    segs: list[tuple[bool, str]] = []
    cur: list[str] = []
    editable = False
    for ln in lines:
        if not editable and START_RE.match(ln):
            cur.append(ln)
            segs.append((False, "".join(cur)))
            cur, editable = [], True
        elif editable and END_RE.match(ln):
            segs.append((True, "".join(cur)))
            cur, editable = [ln], False
        else:
            cur.append(ln)
    if editable:
        raise EditError("an OMEGA-BLOCK-START marker has no matching OMEGA-BLOCK-END")
    segs.append((False, "".join(cur)))
    return segs


def editable_blocks(code: str) -> list[str]:
    return [t for ed, t in split_segments(code) if ed]


def fixed_parts(code: str) -> list[str]:
    return [t for ed, t in split_segments(code) if not ed]


def editable_text(code: str) -> str:
    """All editable regions joined; used for size features and inspirations."""
    return "\n...\n".join(b.rstrip("\n") for b in editable_blocks(code))


def check_fixed_unchanged(original: str, new: str) -> None:
    try:
        new_fixed = fixed_parts(new)
    except EditError as e:
        raise EditError(f"edit broke the block markers: {e}") from None
    if new_fixed != fixed_parts(original):
        raise EditError(
            "edit changed code outside the OMEGA-BLOCK regions (or the markers); "
            "only code between OMEGA-BLOCK-START and OMEGA-BLOCK-END may change"
        )


@dataclass
class ParsedResponse:
    idea: str = ""
    edits: list[tuple[str, str]] = field(default_factory=list)
    code: str | None = None  # rewrite-mode payload


def strip_thinking(text: str) -> str:
    text = _THINK_RE.sub("", text)
    # An unterminated <think> (truncated output) leaves nothing usable before it.
    if "</think>" in text.lower():
        text = text[text.lower().rindex("</think>") + len("</think>"):]
    return text


def parse_response(text: str) -> ParsedResponse:
    text = strip_thinking(text)
    out = ParsedResponse()
    m = _IDEA_RE.search(text)
    if m:
        out.idea = m.group(1).strip().strip("*").strip()[:400]
    out.edits = [(s, r) for s, r in _EDIT_RE.findall(text)]
    if not out.edits:
        fences = _FENCE_RE.findall(text)
        if fences:
            out.code = max(fences, key=len)
    return out


def _find_fuzzy(code_lines: list[str], search_lines: list[str]) -> list[tuple[int, int]]:
    """Line spans [start, end) matching ``search_lines`` when indentation, trailing
    whitespace and blank lines are ignored (small models often get those wrong)."""
    want = [s.strip() for s in search_lines if s.strip()]
    if not want:
        return []
    nonblank = [(i, c.strip()) for i, c in enumerate(code_lines) if c.strip()]
    texts = [t for _, t in nonblank]
    n = len(want)
    return [(nonblank[k][0], nonblank[k + n - 1][0] + 1)
            for k in range(len(texts) - n + 1) if texts[k:k + n] == want]


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def apply_edits(code: str, edits: list[tuple[str, str]]) -> str:
    """Apply SEARCH/REPLACE edits in order; exact match first, then fuzzy."""
    new = code
    for k, (search, replace) in enumerate(edits, 1):
        if not search.strip():
            raise EditError(f"edit {k}: empty SEARCH section")
        hits = new.count(search)
        if hits == 1:
            new = new.replace(search, replace, 1)
            continue
        if hits > 1:
            raise EditError(f"edit {k}: SEARCH text occurs {hits} times; include more context lines")
        lines = new.splitlines(keepends=True)
        s_lines = search.splitlines()
        spans = _find_fuzzy(lines, s_lines)
        if len(spans) != 1:
            first = next((s for s in s_lines if s.strip()), "")
            why = "was not found" if not spans else f"matches {len(spans)} places"
            raise EditError(
                f"edit {k}: SEARCH text {why} in the current program "
                f"(first line: {first.strip()[:80]!r}); copy lines exactly"
            )
        start, end = spans[0]
        # Re-indent the replacement by the indentation offset of the first line.
        s_first = next(s for s in s_lines if s.strip())
        have_ind, want_ind = _indent(lines[start]), _indent(s_first)
        r_lines = replace.splitlines()
        if have_ind != want_ind:
            fixed = []
            for r in r_lines:
                if r.strip() and r.startswith(want_ind):
                    r = have_ind + r[len(want_ind):]
                elif r.strip() and not want_ind:
                    r = have_ind + r
                fixed.append(r)
            r_lines = fixed
        r_lines = _trimmed(r_lines)
        end_nl = "\n" if lines[end - 1].endswith("\n") else ""
        rep = "\n".join(r_lines) + (end_nl if r_lines else "")
        new = "".join(lines[:start]) + rep + "".join(lines[end:])
    check_fixed_unchanged(code, new)
    return new


def _trimmed(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def apply_rewrite(code: str, payload: str) -> str:
    """Replace the editable region(s) with a rewrite-mode payload.

    The payload may be the full file (markers included) or just the new body
    of the single editable region.
    """
    segs = split_segments(code)
    n_blocks = sum(1 for ed, _ in segs if ed)
    p_lines = payload.splitlines()
    has_start = any(START_RE.match(ln) for ln in p_lines)
    has_end = any(END_RE.match(ln) for ln in p_lines)
    if n_blocks == 1 and has_start != has_end:
        payload = _region_by_context(payload, segs[0][1], segs[2][1], has_start)
        has_start = False
    if has_start:
        new_blocks = editable_blocks(payload)
        if len(new_blocks) != n_blocks:
            raise EditError(
                f"rewrite has {len(new_blocks)} OMEGA-BLOCK regions, program has {n_blocks}"
            )
    elif n_blocks == 1:
        new_blocks = [payload]
    else:
        raise EditError(
            f"program has {n_blocks} OMEGA-BLOCK regions; a rewrite must contain the "
            "full file with all markers"
        )
    it = iter(new_blocks)
    out = []
    for ed, text in segs:
        if ed:
            b = next(it)
            out.append(b if b.endswith("\n") or not b else b + "\n")
        else:
            out.append(text)
    new = "".join(out)
    check_fixed_unchanged(code, new)
    return new


def _has_markers(text: str) -> bool:
    lines = text.splitlines()
    return any(START_RE.match(ln) for ln in lines) and any(END_RE.match(ln) for ln in lines)


def _last_per_anchor(edits: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Keep only the last edit per first SEARCH line: models sometimes restate an
    edit several times while correcting themselves."""
    def anchor(s: str) -> str:
        return next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    last = {anchor(s): i for i, (s, _) in enumerate(edits)}
    return [e for i, e in enumerate(edits) if last[anchor(e[0])] == i]


def apply_answer(code: str, parsed: ParsedResponse) -> str:
    """Apply a parsed answer, salvaging the common near-misses of small models."""
    if parsed.edits:
        try:
            return apply_edits(code, parsed.edits)
        except EditError as first:
            # An edit whose REPLACE holds the whole region, markers included.
            for _, rep in reversed(parsed.edits):
                if _has_markers(rep):
                    try:
                        return apply_rewrite(code, rep)
                    except EditError:
                        pass
            dedup = _last_per_anchor(parsed.edits)
            if len(dedup) < len(parsed.edits):
                try:
                    return apply_edits(code, dedup)
                except EditError:
                    pass
            raise first
    if parsed.code is not None:
        return apply_rewrite(code, parsed.code)
    raise EditError("answer contained neither SEARCH/REPLACE blocks nor a code block")


def _anchor(fixed: str, last: bool) -> str:
    """First (or last) substantive line of a fixed segment, markers excluded."""
    lines = [ln.strip() for ln in fixed.splitlines()
             if ln.strip() and not START_RE.match(ln) and not END_RE.match(ln)]
    return (lines[-1] if last else lines[0]) if lines else ""


def _region_by_context(payload: str, head: str, tail: str, has_start: bool) -> str:
    """Cut the editable region out of a full-file rewrite that lost one marker,
    using the fixed code around the region to find where it ends (or begins)."""
    lines = payload.splitlines(keepends=True)
    idx = [i for i, ln in enumerate(lines) if (START_RE if has_start else END_RE).match(ln)]
    if has_start:
        body = lines[idx[0] + 1:]
        stop = _anchor(tail, last=False)
        cut = next((i for i, ln in enumerate(body) if stop and ln.strip() == stop), len(body))
        return "".join(body[:cut]).rstrip() + "\n"
    body = lines[:idx[0]]
    start = _anchor(head, last=True)
    cut = max((i for i, ln in enumerate(body) if start and ln.strip() == start), default=-1)
    return "".join(body[cut + 1:]).strip("\n") + "\n"


def unified_diff(old: str, new: str, name: str = "program") -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            f"a/{name}", f"b/{name}", n=2,
        )
    )

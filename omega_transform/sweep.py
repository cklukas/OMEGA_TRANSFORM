# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Parameter sweeps: numeric tuning without an LLM call per variant.

The LLM decides *what* to tune; the engine does the tuning. An answer may end
with a SWEEP section naming constants of the program and their value space::

    SWEEP:
    STEP_SIZE = logrange(1e-4, 1e-1)
    ITERATIONS = intrange(100, 2000)
    LAYOUT = ["hex", "grid", "ring"]

Supported specs: an explicit list, ``range(lo, hi)`` (uniform float),
``logrange(lo, hi)`` (log-uniform, lo > 0) and ``intrange(lo, hi)`` (inclusive
integers). Each name must be a constant assigned exactly once inside an
OMEGA-BLOCK region: ``NAME = 0.5`` in Python, ``constexpr double NAME = 0.5;``
(or ``const``, brace initialization) or ``#define NAME 0.5`` in C/C++.

Small, all-list spaces are evaluated as a full grid. Otherwise half the
budget samples the space at random, and the rest refines around the best
point found so far with a shrinking neighborhood. Points are evaluated in
batches of the evaluator's concurrency.
"""
from __future__ import annotations

import ast
import itertools
import math
import random
import re
from dataclasses import dataclass, field

from .blocks import EditError, check_fixed_unchanged

MAX_PARAMS = 4

_SWEEP_RE = re.compile(r"^[ \t>*_#-]*SWEEP[*_]*[ \t]*:[ \t]*$", re.M | re.I)
_LINE_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*[=:]\s*(.+?)\s*$")
_FUNC_RE = re.compile(r"^(range|logrange|intrange)\s*\((.*)\)$")


@dataclass
class Param:
    name: str
    kind: str  # list | range | logrange | intrange
    values: list = field(default_factory=list)  # list kind
    lo: float = 0.0
    hi: float = 0.0

    def sample(self, rng: random.Random):
        if self.kind == "list":
            return rng.choice(self.values)
        if self.kind == "range":
            return rng.uniform(self.lo, self.hi)
        if self.kind == "logrange":
            return math.exp(rng.uniform(math.log(self.lo), math.log(self.hi)))
        return rng.randint(int(self.lo), int(self.hi))

    def perturb(self, v, scale: float, rng: random.Random):
        """A value near ``v``; ``scale`` is a fraction of the parameter's range."""
        if self.kind == "list":
            i = self.values.index(v) if v in self.values else 0
            step = max(1, round(scale * len(self.values)))
            return self.values[min(len(self.values) - 1, max(0, i + rng.randint(-step, step)))]
        if self.kind == "logrange":
            lo, hi, x = math.log(self.lo), math.log(self.hi), math.log(v)
            return math.exp(min(hi, max(lo, x + rng.gauss(0, scale * (hi - lo)))))
        x = v + rng.gauss(0, scale * (self.hi - self.lo))
        x = min(self.hi, max(self.lo, x))
        return int(round(x)) if self.kind == "intrange" else x


def parse_sweep(text: str) -> list[Param]:
    """Extract the SWEEP section of an LLM answer (empty list if there is none)."""
    m = None
    for m in _SWEEP_RE.finditer(text):
        pass  # the last SWEEP: header wins
    if m is None:
        return []
    params: list[Param] = []
    for line in text[m.end():].splitlines():
        s = line.strip().strip("`")
        if not s:
            if params:
                break
            continue
        if s.startswith(("<<<<<<<", "```")):
            break
        lm = _LINE_RE.match(s)
        if not lm:
            break
        name, spec = lm.group(1), lm.group(2).rstrip(",;")
        params.append(_parse_spec(name, spec))
        if len(params) > MAX_PARAMS:
            raise EditError(f"SWEEP: at most {MAX_PARAMS} parameters")
    return params


def _parse_spec(name: str, spec: str) -> Param:
    fm = _FUNC_RE.match(spec)
    try:
        if fm:
            args = ast.literal_eval(f"({fm.group(2)},)")
            if len(args) != 2:
                raise ValueError
            lo, hi = float(args[0]), float(args[1])
            if hi < lo:
                lo, hi = hi, lo
            if fm.group(1) == "logrange" and lo <= 0:
                raise EditError(f"SWEEP {name}: logrange needs lo > 0")
            return Param(name, fm.group(1), lo=lo, hi=hi)
        values = ast.literal_eval(spec)
    except (ValueError, SyntaxError):
        raise EditError(f"SWEEP {name}: cannot parse {spec!r}") from None
    if not isinstance(values, (list, tuple)) or not values:
        raise EditError(f"SWEEP {name}: expected a list or range(...), got {spec!r}")
    if not all(isinstance(v, (int, float, str, bool)) for v in values):
        raise EditError(f"SWEEP {name}: list values must be numbers or strings")
    return Param(name, "list", values=list(dict.fromkeys(values)))


# ------------------------------------------------------------ substitution
def _py_pattern(name: str) -> re.Pattern:
    return re.compile(rf"^([ \t]*{re.escape(name)}[ \t]*(?::[^=\n]+)?=[ \t]*)([^#\n]*?)([ \t]*(?:#.*)?)$", re.M)


def _c_patterns(name: str) -> list[re.Pattern]:
    n = re.escape(name)
    return [
        re.compile(rf"^([ \t]*#[ \t]*define[ \t]+{n}[ \t]+)([^\n]*?)([ \t]*(?://.*)?)$", re.M),
        re.compile(rf"((?:^|[;{{}}\s])(?:static\s+|inline\s+)*(?:constexpr|const)\s+"
                   rf"([\w:<>, ]+?)\s+{n}\s*(?:=\s*|\{{\s*))([^;}}]+?)(\s*[;}}])", re.M),
    ]


def _literal(value, lang: str, ctype: str = "") -> str:
    if isinstance(value, bool):
        return ("True" if value else "False") if lang == "python" else ("true" if value else "false")
    if isinstance(value, str):
        return repr(value) if lang == "python" else '"' + value.replace('"', '\\"') + '"'
    if isinstance(value, int) or (isinstance(value, float) and "int" in ctype):
        return str(int(round(value)))
    text = repr(float(value))
    if lang != "python":
        if "e" not in text and "." not in text and "inf" not in text:
            text += ".0"
        if "float" in ctype:
            text += "f"
    return text


def substitute(code: str, values: dict, lang: str) -> str:
    """Set each named constant to its value; fixed regions must stay unchanged."""
    new = code
    for name, value in values.items():
        if lang == "python":
            pat = _py_pattern(name)
            hits = pat.findall(new)
            if len(hits) != 1:
                raise EditError(f"SWEEP {name}: expected exactly one top-level assignment "
                                f"'{name} = ...', found {len(hits)}")
            lit = _literal(value, lang)
            new = pat.sub(lambda m: m.group(1) + lit + m.group(3), new)
            continue
        define, const = _c_patterns(name)
        if len(define.findall(new)) == 1:
            lit = _literal(value, lang)
            new = define.sub(lambda m: m.group(1) + lit + m.group(3), new)
        elif len(const.findall(new)) == 1:
            new = const.sub(lambda m: m.group(1) + _literal(value, lang, m.group(2)) + m.group(4), new)
        else:
            raise EditError(f"SWEEP {name}: expected exactly one 'constexpr/const <type> {name} = ...;' "
                            f"or '#define {name} ...'")
    check_fixed_unchanged(code, new)
    return new


# ---------------------------------------------------------------- planning
def grid_points(params: list[Param], budget: int) -> list[dict] | None:
    if not all(p.kind == "list" for p in params):
        return None
    size = math.prod(len(p.values) for p in params)
    if size > budget:
        return None
    return [dict(zip([p.name for p in params], combo))
            for combo in itertools.product(*[p.values for p in params])]


def fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def fmt_point(point: dict) -> str:
    return ", ".join(f"{k}={fmt_value(v)}" for k, v in point.items())


class Sweep:
    """Proposes sweep points batch by batch, learning from reported scores."""

    def __init__(self, params: list[Param], budget: int, rng: random.Random):
        self.params, self.budget, self.rng = params, budget, rng
        self.grid = grid_points(params, budget)
        self.results: list[tuple[dict, float | None]] = []
        self._seen: set[tuple] = set()
        self._issued = 0

    def _key(self, point: dict) -> tuple:
        return tuple(round(v, 12) if isinstance(v, float) else v for v in point.values())

    def remaining(self) -> int:
        return (len(self.grid) if self.grid is not None else self.budget) - self._issued

    def next_batch(self, size: int) -> list[dict]:
        if self.grid is not None:
            batch = self.grid[self._issued:self._issued + size]
            self._issued += len(batch)
            return batch
        batch, tries = [], 0
        while len(batch) < min(size, self.remaining()) and tries < 50 * size:
            tries += 1
            best = self.best()
            explore = self._issued + len(batch) < self.budget // 2 or best is None
            if explore:
                point = {p.name: p.sample(self.rng) for p in self.params}
            else:
                # Neighborhood shrinks as the refinement phase proceeds.
                done = (self._issued + len(batch) - self.budget // 2) / max(1, self.budget - self.budget // 2)
                scale = 0.25 * (1 - done) + 0.03
                point = {p.name: p.perturb(best[0][p.name], scale, self.rng) for p in self.params}
            k = self._key(point)
            if k in self._seen:
                continue
            self._seen.add(k)
            batch.append(point)
        self._issued += len(batch)
        return batch

    def report(self, point: dict, score: float | None) -> None:
        self.results.append((point, score))

    def best(self) -> tuple[dict, float] | None:
        ok = [(p, s) for p, s in self.results if s is not None]
        return max(ok, key=lambda t: t[1]) if ok else None

    def summary(self, base_score: float | None, max_len: int = 600) -> str:
        """Compact text for the LLM: best and worst points, failures, per-value means."""
        ok = sorted([(p, s) for p, s in self.results if s is not None], key=lambda t: -t[1])
        failed = len(self.results) - len(ok)
        if not ok:
            return f"sweep of {len(self.results)} points: all failed"
        parts = [f"sweep of {len(self.results)} points"
                 + (f" ({failed} failed)" if failed else "")
                 + f", scores {ok[-1][1]:.6g}..{ok[0][1]:.6g}"
                 + (f" (unswept {base_score:.6g})" if base_score is not None else "")]
        parts.append("best: " + "; ".join(f"{fmt_point(p)} -> {s:.6g}" for p, s in ok[:3]))
        parts.append(f"worst: {fmt_point(ok[-1][0])} -> {ok[-1][1]:.6g}")
        for prm in self.params:
            if prm.kind == "list" and len(prm.values) > 1:
                means = []
                for v in prm.values:
                    ss = [s for p, s in ok if p[prm.name] == v]
                    if ss:
                        means.append(f"{fmt_value(v)}:{sum(ss) / len(ss):.4g}")
                parts.append(f"mean by {prm.name}: " + " ".join(means))
        text = " | ".join(parts)
        return text if len(text) <= max_len else text[: max_len - 1] + "…"

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Adaptive choice of prompt style and edit mode (Thompson sampling).

Each arm (style ``refine``/``bold``/``tune``, edit ``diff``/``rewrite``) keeps
a count of LLM answers and of answers that beat their parent. A choice draws
``ratio * Beta(1 + wins, 1 + losses)`` per arm and picks the largest share at
random in proportion. With little data the draws are close to uniform, so the
configured ratios dominate; with data, arms that keep failing fade without
ever reaching zero.
"""
from __future__ import annotations

import random
import threading

from .database import Program


def arms_of(p: Program) -> tuple[str, str] | None:
    """(style, edit) of an LLM answer, or None for entries that are not one."""
    parts = p.mode.split("/")
    if parts and parts[0] == "sweep":
        return None  # credited through the base program the sweep started from
    if len(parts) != 3 or parts[2] == "fix":
        return None  # initial program, repairs and retries are not policy choices
    return parts[2], parts[1]


class Policy:
    def __init__(self, style_ratios: dict[str, float], edit_ratios: dict[str, float],
                 adaptive: bool = True):
        self.style_ratios = {k: v for k, v in style_ratios.items() if v > 0}
        self.edit_ratios = {k: v for k, v in edit_ratios.items() if v > 0}
        self.adaptive = adaptive
        self.wins: dict[str, int] = {}
        self.tries: dict[str, int] = {}
        self._lock = threading.Lock()

    def observe(self, p: Program, parent_score: float | None, improved_by_sweep: bool = False) -> None:
        arms = arms_of(p)
        if arms is None:
            return
        won = improved_by_sweep or (p.ok and parent_score is not None and p.score > parent_score)
        with self._lock:
            for a in arms:
                self.tries[a] = self.tries.get(a, 0) + 1
                self.wins[a] = self.wins.get(a, 0) + (1 if won else 0)

    def _choose(self, ratios: dict[str, float], rng: random.Random) -> str:
        if not self.adaptive:
            return rng.choices(list(ratios), weights=list(ratios.values()))[0]
        with self._lock:
            w = [r * rng.betavariate(1 + self.wins.get(a, 0),
                                     1 + self.tries.get(a, 0) - self.wins.get(a, 0))
                 for a, r in ratios.items()]
        return rng.choices(list(ratios), weights=w)[0]

    def style(self, rng: random.Random) -> str:
        return self._choose(self.style_ratios, rng)

    def edit(self, rng: random.Random) -> str:
        return self._choose(self.edit_ratios, rng)

    def summary(self) -> str:
        with self._lock:
            return "  ".join(f"{a} {self.wins.get(a, 0)}/{self.tries.get(a, 0)}"
                             for a in [*self.style_ratios, *self.edit_ratios])

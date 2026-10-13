# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import random
import unittest
from collections import Counter

from omega_transform.database import Program
from omega_transform.policy import Policy


def answer(style, edit, score):
    return Program(id="x", code="", mode=f"exploit/{edit}/{style}", status="ok", score=score)


class PolicyTest(unittest.TestCase):
    def test_learns_to_prefer_winning_style(self):
        pol = Policy({"refine": 0.5, "bold": 0.5}, {"diff": 1.0})
        for _ in range(40):
            pol.observe(answer("refine", "diff", 2.0), parent_score=1.0)  # always improves
            pol.observe(answer("bold", "diff", 0.5), parent_score=1.0)  # never improves
        rng = random.Random(0)
        picks = Counter(pol.style(rng) for _ in range(1000))
        self.assertGreater(picks["refine"], 900)
        self.assertGreater(picks["bold"], 0)  # never fully abandoned

    def test_ignores_non_policy_entries(self):
        pol = Policy({"refine": 1.0}, {"diff": 1.0})
        pol.observe(Program(id="i", code="", mode="initial"), None)
        pol.observe(Program(id="r", code="", mode="retry/diff/fix"), 1.0)
        pol.observe(Program(id="s", code="", mode="sweep/exploit/diff/tune"), 1.0)
        self.assertEqual(pol.tries, {})

    def test_fixed_ratios_when_not_adaptive(self):
        pol = Policy({"refine": 0.7, "bold": 0.3}, {"diff": 1.0}, adaptive=False)
        for _ in range(50):
            pol.observe(answer("bold", "diff", 0.0), parent_score=1.0)
        picks = Counter(pol.style(random.Random(i)) for i in range(2000))
        self.assertAlmostEqual(picks["bold"] / 2000, 0.3, delta=0.05)


if __name__ == "__main__":
    unittest.main()

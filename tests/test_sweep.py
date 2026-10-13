# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import random
import tempfile
import unittest
from pathlib import Path

from omega_transform.blocks import EditError
from omega_transform.config import load_config
from omega_transform.engine import Engine
from omega_transform.llm import ScriptedLLM
from omega_transform.sweep import Sweep, parse_sweep, substitute

TOY = Path(__file__).resolve().parent.parent / "examples" / "toy"

PY = """# OMEGA-BLOCK-START
STEP = 0.5  # learning rate
N_ITER: int = 10
MODE = "a"
# OMEGA-BLOCK-END
LIMIT = 3
"""

CPP = """#include <cstdio>
// OMEGA-BLOCK-START
constexpr double ALPHA = 0.5;
static const float BETA{1.5f};
constexpr int TILE = 32;
#define UNROLL 4
// OMEGA-BLOCK-END
int main() {}
"""


class SweepTest(unittest.TestCase):
    def test_parse(self):
        text = """IDEA: tune it
<<<<<<< SEARCH
a
=======
b
>>>>>>> REPLACE
SWEEP:
STEP = logrange(1e-3, 1)
N_ITER = intrange(5, 50)
MODE = ["a", "b"]
"""
        ps = parse_sweep(text)
        self.assertEqual([p.name for p in ps], ["STEP", "N_ITER", "MODE"])
        self.assertEqual([p.kind for p in ps], ["logrange", "intrange", "list"])
        self.assertEqual(parse_sweep("no sweep here"), [])
        with self.assertRaises(EditError):
            parse_sweep("SWEEP:\nX = logrange(0, 1)\n")

    def test_substitute_python(self):
        new = substitute(PY, {"STEP": 0.01, "N_ITER": 20, "MODE": "b"}, "python")
        self.assertIn("STEP = 0.01  # learning rate", new)
        self.assertIn("N_ITER: int = 20", new)
        self.assertIn("MODE = 'b'", new)
        with self.assertRaises(EditError):  # outside the editable block
            substitute(PY, {"LIMIT": 4}, "python")

    def test_substitute_cpp(self):
        new = substitute(CPP, {"ALPHA": 0.25, "BETA": 2.0, "TILE": 64.4, "UNROLL": 8}, "cpp")
        self.assertIn("constexpr double ALPHA = 0.25;", new)
        self.assertIn("static const float BETA{2.0f};", new)
        self.assertIn("constexpr int TILE = 64;", new)
        self.assertIn("#define UNROLL 8", new)

    def test_grid_and_refine(self):
        ps = parse_sweep('SWEEP:\nA = [1, 2, 3]\nB = ["x", "y"]\n')
        s = Sweep(ps, 16, random.Random(0))
        pts = s.next_batch(100)
        self.assertEqual(len(pts), 6)
        self.assertEqual(s.remaining(), 0)
        ps = parse_sweep("SWEEP:\nX = range(0, 10)\n")
        s = Sweep(ps, 20, random.Random(1))
        while s.remaining() > 0:
            for pt in s.next_batch(4):
                s.report(pt, -(pt["X"] - 7.3) ** 2)
        self.assertLess(abs(s.best()[0]["X"] - 7.3), 0.5)
        self.assertIn("sweep of 20 points", s.summary(None))

    def test_engine_sweep(self):
        def llm(messages):
            user = messages[-1]["content"]
            if user.startswith("You maintain"):
                return "- ok"
            if "K = " in user.split("## CURRENT PROGRAM", 1)[1]:
                return "IDEA: nothing new\n```python\ndef value():\n    return 0\n```"
            return ("IDEA: expose the constant\n<<<<<<< SEARCH\ndef value():\n    return 1\n"
                    "=======\nK = 5\n\ndef value():\n    return K\n>>>>>>> REPLACE\n"
                    "SWEEP:\nK = intrange(0, 120)\n")

        cfg = load_config(TOY)
        cfg.search.iterations = 1
        cfg.search.sweep_budget = 24
        cfg.search.repair_ratio = 0.0
        with tempfile.TemporaryDirectory() as tmp:
            eng = Engine(cfg, Path(tmp), llm=ScriptedLLM(llm), log=lambda s: None)
            self.addCleanup(eng.close)
            best = eng.run()
            self.assertTrue(best.idea.startswith("tuned K="), best.idea)
            self.assertGreater(best.score, 90)
            counts = eng.db.status_counts()
            self.assertGreater(counts.get("swept", 0), 10)
            base = eng.db.get(best.parent_id)
            self.assertEqual(base.score, 5)
            self.assertIn("sweep of 24 points", base.feedback)
            # Swept variants stay out of what the LLM sees.
            self.assertEqual([c.id for c in eng.db.children(base.id)], [best.id])


if __name__ == "__main__":
    unittest.main()

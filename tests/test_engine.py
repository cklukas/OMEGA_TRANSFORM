# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import re
import tempfile
import unittest
from pathlib import Path

from omega_transform.config import load_config
from omega_transform.engine import Engine
from omega_transform.llm import ScriptedLLM

TOY = Path(__file__).resolve().parent.parent / "examples" / "toy"


def incrementer(messages):
    """Reads the current program from the prompt and bumps the returned value;
    every third answer is broken in a different way."""
    user = messages[-1]["content"]
    if user.startswith("You maintain the lab notebook"):
        return "- incrementing works"
    cur = user.split("## CURRENT PROGRAM", 1)[1]
    m = re.search(r"    return (-?\d+)", cur)
    v = int(m.group(1)) if m else 1
    incrementer.calls += 1
    if incrementer.calls % 7 == 0:
        return "IDEA: break it\n<<<<<<< SEARCH\nnot in program\n=======\nx\n>>>>>>> REPLACE"
    if incrementer.calls % 5 == 0:
        return f"IDEA: syntax error\n<<<<<<< SEARCH\n    return {v}\n=======\n    return (\n>>>>>>> REPLACE"
    return (f"IDEA: return {v + 3} instead of {v}\n<<<<<<< SEARCH\n    return {v}\n=======\n"
            f"    return {v + 3}\n>>>>>>> REPLACE")


incrementer.calls = 0


class EngineTest(unittest.TestCase):
    def test_toy_run(self):
        cfg = load_config(TOY)
        cfg.search.iterations = 24
        cfg.search.insights_interval = 8
        cfg.evaluator.concurrency = 3
        logs = []
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        eng = Engine(cfg, Path(tmp), llm=ScriptedLLM(incrementer), log=logs.append)
        self.addCleanup(eng.close)
        best = eng.run()
        self.assertIsNotNone(best)
        self.assertGreaterEqual(best.score, 10)
        counts = eng.db.status_counts()
        self.assertGreater(counts.get("ok", 0), 5)
        self.assertIn("edit_error", counts)
        self.assertTrue(counts.get("runtime_error", 0) + counts.get("invalid", 0) > 0)
        self.assertEqual(eng.db.max_iteration(), 23)
        self.assertIn("incrementing works", eng.db.latest_note())
        # A crashed candidate gets an immediate fix-up attempt as its child.
        retries = [p for p in eng.db.all_programs() if p.mode.startswith("retry/")]
        self.assertTrue(retries)
        self.assertTrue(all(eng.db.get(r.parent_id).status in ("runtime_error", "invalid")
                            for r in retries))
        lineage = eng.db.lineage(best.id)
        self.assertEqual(lineage[0].idea, "initial program")
        # Resuming continues the iteration numbering.
        eng2 = Engine(cfg, Path(tmp), llm=ScriptedLLM(incrementer), log=logs.append)
        self.addCleanup(eng2.close)
        eng2.run(iterations=4)
        self.assertEqual(eng2.db.max_iteration(), 27)
        # Failed attempts appear in later prompts so the LLM can learn from them.
        prompts = "".join(p.read_text(encoding="utf-8") for p in (Path(tmp) / "llm").glob("*.md"))
        self.assertIn("FAILED", prompts)
        # Logged prompts can be replayed against their original parents.
        from omega_transform.replay import replay
        res = replay(Path(tmp), eng2.db, ScriptedLLM(incrementer), n=5, evaluator=eng2.evaluator)
        self.assertEqual(len(res), 5)
        self.assertTrue(any(r.outcome == "ok" for r in res))


class FailingModelTest(unittest.TestCase):
    def test_run_stops_after_consecutive_llm_errors(self):
        from omega_transform.llm import LLMError

        def broken(messages):
            raise LLMError("model does not support thinking")

        cfg = load_config(TOY)
        cfg.search.iterations = 1000
        cfg.llm.max_consecutive_errors = 5
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        eng = Engine(cfg, Path(tmp), llm=ScriptedLLM(broken), log=lambda s: None)
        self.addCleanup(eng.close)
        eng.run()
        self.assertLess(eng.db.count("llm_error"), 20)


class ConfirmTest(unittest.TestCase):
    def test_noisy_best_is_confirmed_by_median(self):
        cfg = load_config(TOY)
        cfg.evaluator.confirm_runs = 2
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        eng = Engine(cfg, Path(tmp), llm=ScriptedLLM(incrementer), log=lambda s: None)
        self.addCleanup(eng.close)
        eng.initialize()
        from omega_transform.database import Program
        p = Program(id="x1", code="# OMEGA-BLOCK-START\ndef value():\n    return 50\n"
                    "# OMEGA-BLOCK-END\n", status="ok", score=99.0)  # lucky first run
        eng._confirm(p)
        self.assertEqual(p.score, 50)  # median of [99, 50, 50]
        self.assertEqual(p.metrics["score_samples"], [50, 50, 99.0])


if __name__ == "__main__":
    unittest.main()


class RecurringErrorTest(unittest.TestCase):
    def test_failures_are_grouped_by_cause(self):
        from omega_transform.database import Program
        from omega_transform.prompt import recurring_errors

        def failed(status, feedback):
            return Program(id="x", code="", status=status, feedback=feedback)

        tb = ('OverflowError: math range error File "initial.py", line 50, in softmin '
              's = sum(math.exp(v) for v in vals)')
        attempts = [failed("invalid", tb), failed("invalid", tb.replace("line 50", "line 77")),
                    failed("compile_error", "x.cpp:3:1: error: use of undeclared identifier 'foo'"),
                    failed("compile_error", "x.cpp:9:2: error: use of undeclared identifier 'bar'"),
                    failed("invalid", 'SyntaxError: invalid syntax (initial.py, line 4) File '
                                      '"<frozen>", line 1, in _call_with_frames_removed'),
                    Program(id="ok", code="", status="ok", score=1.0)]
        self.assertEqual(recurring_errors(attempts), [
            ("OverflowError in softmin()", 2),
            ("compile error: use of undeclared identifier '…'", 2)])

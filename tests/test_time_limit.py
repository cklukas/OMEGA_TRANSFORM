# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""The time limit holds: an answer still being written is abandoned at it
(llm.TimeUp), the engine ends the run without counting that answer against the
model, and the report counts a run with what it had at its limit
(report.within_budget)."""
import json
import random
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from omega_transform import llm
from omega_transform.config import LLMConfig, ModelSpec, load_config
from omega_transform.engine import Engine
from omega_transform.report import within_budget

TOY = Path(__file__).resolve().parent.parent / "examples" / "toy"


class EndlessOllama:
    """A local /api/chat that streams one short piece every 50 ms and never ends."""

    def __init__(self):
        self.closed = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    for _ in range(2000):
                        line = {"message": {"content": "word "}, "done": False}
                        self.wfile.write((json.dumps(line) + "\n").encode())
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:  # the client left: BrokenPipe, reset, or aborted (Windows)
                    outer.closed.set()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AbandonTest(unittest.TestCase):
    def test_an_answer_is_abandoned_at_the_deadline(self):
        server = EndlessOllama()
        self.addCleanup(server.close)
        cfg = LLMConfig(base_url=server.base, models=[ModelSpec("endless")])
        t0 = time.monotonic()
        with self.assertRaises(llm.TimeUp) as cm:
            llm.Ensemble(cfg).complete([{"role": "user", "content": "hi"}], random.Random(),
                                       deadline=t0 + 0.5)
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertIn("word", cm.exception.partial)
        # Leaving the stream closes the connection, so the server stops writing.
        self.assertTrue(server.closed.wait(5.0))

    def test_no_request_starts_after_the_deadline(self):
        cfg = LLMConfig(base_url="http://127.0.0.1:9", models=[ModelSpec("m")])
        with self.assertRaises(llm.TimeUp):
            llm.Ensemble(cfg).complete([{"role": "user", "content": "hi"}], random.Random(),
                                       deadline=time.monotonic() - 1)


class CutOffModel:
    """Answers like a model that improves each time, until the third answer, which
    the time limit cuts off."""

    def __init__(self):
        self.calls, self.deadlines = 0, []
        self.tokens_in = self.tokens_out = 0

    def complete(self, messages, rng, model=None, max_chars=0, deadline=None):
        self.deadlines.append(deadline)
        self.calls += 1
        if self.calls >= 3:
            raise llm.TimeUp("m: answer abandoned at the time limit", "IDEA: half")
        cur = messages[-1]["content"].split("## CURRENT PROGRAM", 1)[1]
        v = int(cur.split("    return ", 1)[1].split()[0])
        return (f"IDEA: return {v + 1}\n<<<<<<< SEARCH\n    return {v}\n=======\n"
                f"    return {v + 1}\n>>>>>>> REPLACE"), "m"

    def check(self, reserve_gb=0.0, measured=None):
        return []


class EngineTest(unittest.TestCase):
    def test_the_run_ends_at_the_limit_without_a_model_error(self):
        cfg = load_config(TOY)
        cfg.evaluator.concurrency = 1
        cfg.search.iterations = 50
        cfg.search.max_minutes = 60
        cfg.search.sweep_budget = 0
        cfg.search.insights_interval = 0
        cfg.search.islands = 1
        model = CutOffModel()
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        eng = Engine(cfg, Path(tmp), llm=model, log=lambda s: None)
        self.addCleanup(eng.close)
        eng.run()
        self.assertEqual(eng.stop_reason, "time limit")
        self.assertEqual(eng.db.count("llm_error"), 0)
        self.assertEqual(model.calls, 3)
        self.assertTrue(all(d is not None for d in model.deadlines))


def run(progress, lineage=None, budget=60.0):
    return {"minutes_budget": budget, "initial_score": 1.0, "progress": progress,
            "best_score": progress[-1][1], "improvement": progress[-1][1],
            "improvements": len(progress) - 1, "minutes_to_best": progress[-1][0],
            "best_lineage": lineage or [], "best_idea": "late idea",
            "best_diff": "--- initial\n+++ best\n", "best_solution": {"x": 1}}


class WithinBudgetTest(unittest.TestCase):
    def test_a_run_within_its_limit_is_unchanged(self):
        r = run([[0.0, 1.0], [30.0, 2.0]])
        self.assertEqual(within_budget(dict(r)), r)

    def test_a_best_after_the_limit_does_not_count(self):
        lineage = [{"minutes": 0.0, "score": 1.0, "idea": "initial program"},
                   {"minutes": 40.0, "score": 2.0, "idea": "early idea"},
                   {"minutes": 75.0, "score": 3.0, "idea": "late idea"}]
        r = within_budget(run([[0.0, 1.0], [40.0, 2.0], [75.0, 3.0]], lineage))
        self.assertTrue(r["cut_at_budget"])
        self.assertEqual(r["best_score"], 2.0)
        self.assertEqual(r["improvement"], 2.0)
        self.assertEqual(r["improvements"], 1)
        self.assertEqual(r["minutes_to_best"], 40.0)
        self.assertEqual(r["progress"], [[0.0, 1.0], [40.0, 2.0]])
        # The best at the limit is an ancestor of the later best: its path is kept.
        self.assertEqual([s["idea"] for s in r["best_lineage"]],
                         ["initial program", "early idea"])
        self.assertEqual(r["best_idea"], "early idea")
        self.assertEqual(r["best_diff"], "")
        self.assertIsNone(r["best_solution"])

    def test_the_best_at_the_limit_on_another_branch_has_no_path(self):
        lineage = [{"minutes": 0.0, "score": 1.0, "idea": "initial program"},
                   {"minutes": 70.0, "score": 3.0, "idea": "late idea"}]
        r = within_budget(run([[0.0, 1.0], [40.0, 2.0], [70.0, 3.0]], lineage))
        self.assertEqual(r["best_score"], 2.0)
        self.assertEqual(r["best_lineage"], [])
        self.assertEqual(r["best_idea"], "")

    def test_no_gain_before_the_limit_keeps_the_start(self):
        r = within_budget(run([[0.0, 1.0], [65.0, 2.0]]))
        self.assertEqual(r["best_score"], 1.0)
        self.assertEqual(r["improvement"], 1.0)
        self.assertIsNone(r["minutes_to_best"])


if __name__ == "__main__":
    unittest.main()

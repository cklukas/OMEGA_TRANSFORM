# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import tempfile
import unittest
from pathlib import Path

from omega_transform.bench import summarize
from omega_transform.config import load_config
from omega_transform.engine import Engine
from omega_transform.llm import ScriptedLLM
from omega_transform.report import render, score_at

from test_engine import TOY, incrementer


class BenchTest(unittest.TestCase):
    def test_summarize_run(self):
        cfg = load_config(TOY)
        cfg.search.iterations = 10
        with tempfile.TemporaryDirectory() as tmp:
            eng = Engine(cfg, Path(tmp), llm=ScriptedLLM(incrementer), log=lambda s: None)
            self.addCleanup(eng.close)
            eng.run()
            s = summarize(eng.db)
        self.assertEqual(s["initial_score"], 1)
        self.assertGreater(s["best_score"], 1)
        self.assertGreaterEqual(s["attempts"], 10)
        self.assertEqual(s["progress"][0][1], 1)
        self.assertEqual(s["progress"][-1][1], s["best_score"])
        # How the best program came about: from the initial program to the best one.
        steps = s["best_lineage"]
        self.assertEqual(steps[0]["mode"], "initial")
        self.assertEqual(steps[-1]["score"], s["best_score"])
        self.assertTrue(all("status" in st and "minutes" in st for st in steps))
        self.assertTrue(s["best_diff"].startswith("--- initial\n+++ best\n"))
        self.assertIn("+", s["best_diff"])

    def test_details_are_added_to_older_result_files(self):
        import json
        from omega_transform.bench import add_details, run_dir_of
        cfg = load_config(TOY)
        cfg.search.iterations = 6
        with tempfile.TemporaryDirectory() as tmp:
            results, runs = Path(tmp) / "results", Path(tmp) / "runs"
            results.mkdir()
            f = results / "20260101-000000_toy_m_no-think.json"
            old = {"schema": 1, "example": cfg.name, "model": "m", "variant": "no-think",
                   "best_score": 3}
            f.write_text(json.dumps(old))
            run_dir = run_dir_of(f, runs, old)
            self.assertEqual(run_dir, runs / "20260101-000000" / "toy" / "m_no-think")
            with Engine(cfg, run_dir, llm=ScriptedLLM(incrementer), log=lambda s: None) as eng:
                eng.run()
                best = summarize(eng.db)["best_score"]
            self.assertEqual(add_details(results, runs, log=lambda s: None), [f])
            r = json.loads(f.read_text())
            self.assertEqual(r["schema"], 2)
            self.assertEqual(r["best_lineage"][-1]["score"], best)
            self.assertTrue(r["best_diff"])
            # Files that have them already are left alone.
            self.assertEqual(add_details(results, runs, log=lambda s: None), [])

    def test_inference_summary(self):
        from omega_transform.bench import inference_summary
        calls = [dict(total_seconds=t, load_seconds=l, prompt_tokens=1000, prompt_seconds=1.0,
                      gen_tokens=500, gen_seconds=10.0, stopped=0)
                 for t, l in ((20, 9.0), (30, 0.1), (40, 0.1))]
        s = inference_summary(calls)
        self.assertEqual((s["load_seconds"], s["median_call_seconds"]), (9.0, 30))
        self.assertEqual((s["prompt_tokens_per_s"], s["gen_tokens_per_s"]), (1000.0, 50.0))

    def test_render(self):
        base = {"example": "toy", "minutes_budget": 10, "initial_score": 1.0,
                "machine": {"cpu": "M1"}, "statuses": {"ok": 3, "edit_error": 1},
                "attempts": 4, "ok": 3, "llm_calls": 4, "mean_llm_seconds": 20}
        results = [
            dict(base, model="a:1b", best_score=2.0, improvement=2.0, started="1",
                 progress=[[0, 1.0], [6, 2.0]], best_idea="did x", model_memory_gb=7.1,
                 gen_tokens_per_s=42.5, load_seconds=12, median_call_seconds=30),
            dict(base, model="b:7b", best_score=3.0, improvement=3.0, started="2",
                 progress=[[0, 1.0], [2, 3.0]]),
            dict(base, model="c:big", error="model failed to load", started="3"),
        ]
        page = render(results)
        self.assertIn("## toy", page)
        self.assertLess(page.index("| b:7b"), page.index("| a:1b"))  # best first
        self.assertIn("xychart-beta", page)
        self.assertIn('x-axis ["b:7b", "a:1b"]', page)
        self.assertIn("failed: model failed to load", page)
        self.assertIn("### Inference speed and memory", page)
        self.assertIn("| a:1b |  | 7.1 | 12 |", page)
        self.assertIn("generation speed (tokens/s)", page)
        self.assertEqual(score_at([[0, 1.0], [6, 2.0]], 5), 1.0)
        self.assertIn("No benchmark results", render([]))

    def test_model_check_section(self):
        check = {"checked": "2026-10-09T21:30:00", "models": [
            {"model": "good:1", "size_gb": 6.6, "verdict": "ok", "hello_ok": True, "edit_ok": True},
            {"model": "bad:2", "size_gb": 7.0, "verdict": "broken", "reason": "said nothing"}]}
        page = render([], check)
        self.assertIn("## Model check", page)
        self.assertIn("| bad:2 |  | 7.0 | broken |", page)

    def test_feasible_names(self):
        import json, tempfile
        from omega_transform.modelcheck import feasible_names
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"models": [{"model": "a", "verdict": "ok"},
                                  {"model": "b", "verdict": "slow"}]}, f)
        self.assertEqual(feasible_names(f.name), {"a"})

    def test_local_and_cloud_models_are_separate_sections(self):
        base = {"example": "toy", "minutes_budget": 5, "initial_score": 1.0, "attempts": 2,
                "ok": 1, "best_score": 2.0}
        page = render([dict(base, model="m:1", provider="ollama", started="1"),
                       dict(base, model="v/x:free", provider="openrouter", started="2",
                            gen_tokens_per_s=150.0, cost_usd=0.0)])
        local, cloud = page.index("### Local models"), page.index("### Cloud models (OpenRouter)")
        self.assertLess(page.index("| m:1 |"), cloud)
        self.assertGreater(page.index("| v/x:free |"), cloud)
        self.assertIn("toy, cloud: best score", page)
        self.assertIn("#### Inference speed and cost", page)
        self.assertLess(local, cloud)

    def test_variants_are_separate_rows(self):
        base = {"example": "toy", "model": "m:1", "minutes_budget": 5, "best_score": 2.0,
                "initial_score": 1.0, "attempts": 1, "ok": 1}
        page = render([dict(base, variant="think", started="1"),
                       dict(base, variant="no-think", started="2", best_score=1.5)])
        self.assertIn("| m:1 (think) |", page)
        self.assertIn("| m:1 (no-think) |", page)


if __name__ == "__main__":
    unittest.main()

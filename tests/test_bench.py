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
        section = page[page.index("## toy"):]
        self.assertLess(section.index("| b:7b"), section.index("| a:1b"))  # best first
        self.assertIn("xychart-beta", page)
        self.assertIn('x-axis ["b:7b", "a:1b"]', page)
        self.assertIn("failed: model failed to load", page)
        self.assertIn("### Inference speed and memory", page)
        self.assertIn("| a:1b |  | 7.1 | 12 |", page)
        self.assertIn("generation speed (tokens/s)", page)
        self.assertEqual(score_at([[0, 1.0], [6, 2.0]], 5), 1.0)
        self.assertIn("No benchmark results", render([]))

    def test_overall_ranking_weights_every_example_equally(self):
        from omega_transform.report import ranking, ranking_section
        def run(example, model, init, best, **kw):
            return dict(example=example, model=model, initial_score=init, best_score=best,
                        improvement=best / init, started="1", **kw)
        by_example = {
            # a huge gain on one example must not outweigh the other examples
            "matmul": [run("matmul", "a", 1, 100), run("matmul", "b", 1, 50)],
            "tsp": [run("tsp", "a", 1, 1.0), run("tsp", "b", 1, 1.2)],
            "sort": [run("sort", "a", 2, 2.2), run("sort", "b", 2, 2.4)],
            "flat": [run("flat", "a", 1, 1), run("flat", "b", 1, 1)],  # no information
        }
        ranked = {e["label"]: e for e in ranking(by_example)}
        self.assertEqual(ranked["a"]["examples"], 3)
        self.assertAlmostEqual(ranked["a"]["share"], (1 + 0 + 0.5) / 3)
        self.assertAlmostEqual(ranked["b"]["share"], (49 / 99 + 1 + 1) / 3)
        self.assertEqual((ranked["a"]["wins"], ranked["b"]["wins"]), (1, 2))
        self.assertEqual(ranked["a"]["no_gain"], 1)
        self.assertEqual([e["label"] for e in ranking(by_example)], ["b", "a"])
        # a failed run counts as no gain
        failed = {"x": [run("x", "a", 1, 2), dict(run("x", "b", 1, 3), error="boom")]}
        self.assertEqual({e["label"]: e["share"] for e in ranking(failed)}, {"a": 1.0, "b": 0.0})
        from omega_transform.report import developer, developer_ranking
        self.assertEqual([developer(m) for m in ("gemma4:e2b", "gpt-oss:20b", "qwen3-coder:30b",
                                                 "nvidia/nemotron-x:free", "unknown:1b",
                                                 "richardyoung/frognano-4b-2609",
                                                 "muse-glimmer:30b")],
                         ["Google", "OpenAI", "Alibaba (Qwen)", "NVIDIA", "other",
                          "Microsoft", "Meta"])
        self.assertEqual([developer(m) for m in ("devstral-small-2:24b", "glm-4.7-flash:latest",
                                                 "granite4.2:30b", "z-ai/glm-5.3:free")],
                         ["Mistral AI", "Z.ai", "IBM", "Z.ai"])
        self.assertEqual([developer(m) for m in ("ki-krugle-jp/llm-jp-4-8b-thinking:q4_k_m",
                                                 "sakana/fugu-max", "robit/kolibri-1")],
                         ["NII (LLM-jp)", "Sakana AI", "Aleph Alpha"])
        self.assertEqual([developer(m) for m in ("ingu627/exaone4.0:32b",
                                                 "Bored/GigaChat3.1-10B-A1.8B-q4_K_M:latest",
                                                 "yandex/YandexGPT-5-Lite-8B-instruct-GGUF:latest",
                                                 "MichelRosselli/apertus:8b-instruct-2509-q4_k_m",
                                                 "frob/sarvam:30b")],
                         ["LG", "Sber", "Yandex", "Swiss AI Initiative", "Sarvam AI"])
        self.assertEqual(developer("nemotron-3.5-lightning:30b-mlx"), "NVIDIA")
        devs = developer_ranking([{"label": "g1", "model": "gemma4:a", "share": 0.9,
                                   "by_example": {"x": 1.0, "y": 0.8}},
                                  {"label": "g2", "model": "gemma4:b", "share": 0.5,
                                   "by_example": {"x": 0.0, "y": 1.0}},
                                  {"label": "o", "model": "gpt-oss:20b", "share": 0.95,
                                   "by_example": {"x": 0.9, "y": 1.0}}])
        self.assertEqual([d["developer"] for d in devs], ["OpenAI", "Google"])
        g = devs[1]
        self.assertEqual((g["models"], g["best_label"]), (2, "g1"))
        self.assertAlmostEqual(g["mean_share"], 0.7)
        self.assertAlmostEqual(g["pick_share"], 1.0)  # best of the family per example
        page = ranking_section([r for rows in by_example.values() for r in rows])
        self.assertIn("## Overall ranking", page)
        from omega_transform.report import country, country_ranking
        self.assertEqual([country(w) for w in ("Google", "Mistral AI", "Swiss AI Initiative",
                                               "Alibaba (Qwen)", "nobody")],
                         ["USA", "France", "Switzerland", "China", "unknown"])
        cr = country_ranking([{"label": "g", "model": "gemma4:a", "share": 0.8},
                              {"label": "o", "model": "gpt-oss:20b", "share": 0.6},
                              {"label": "q", "model": "qwen3:8b", "share": 0.6},
                              {"label": "m", "model": "devstral:24b", "share": 0.2}])
        self.assertEqual([(c["country"], c["label"], c["models"]) for c in cr],
                         [("USA", "g", 2), ("China", "q", 1), ("France", "m", 1)])
        self.assertAlmostEqual(cr[1]["lag"], 0.25)
        self.assertAlmostEqual(cr[2]["lag"], 0.75)
        self.assertIn("### Models by developer", page)
        self.assertIn("| other | a, b | – |", page)
        from omega_transform.report import DEVELOPERS, DEVELOPER_INFO
        self.assertFalse({who for _, who in DEVELOPERS} - set(DEVELOPER_INFO),
                         "every developer needs a profile")
        g = ranking_section([dict(run("x", "gemma4:e2b", 1, 2)), dict(run("x", "gpt-oss:20b", 1, 3))])
        self.assertIn("- **Google** (founded 1998, Mountain View, USA):", g)
        self.assertNotIn("**Mistral AI**", g)  # only developers with results
        self.assertIn("| 1 | b | **83%** |", page)

    def test_timeline_by_developer_and_size(self):
        from omega_transform.releases import params_b
        from omega_transform.report import size_class, timeline, timeline_section
        self.assertEqual([params_b(x) for x in ("25.2B", "350M", "nvidia/nemotron-3-super-120b-a12b:free",
                                                "qwen3.5:latest", None)],
                         [25.2, 0.35, 120.0, None, None])
        self.assertEqual([size_class(x) for x in (7.5, 15.0, 30.0, None)],
                         ["under 15B", "15B and up", "15B and up", "size unknown"])
        ranked = [{"label": "a (no-think)", "model": "qwen-a", "share": 0.4},
                  {"label": "a (think)", "model": "qwen-a", "share": 0.5},
                  {"label": "b (no-think)", "model": "qwen-b", "share": 0.3},
                  {"label": "c (no-think)", "model": "qwen-c", "share": 0.7},
                  {"label": "d (no-think)", "model": "qwen-d", "share": 0.9}]
        releases = {"qwen-a": {"released": "2025-01-01", "params_b": 30},
                    "qwen-b": {"released": "2026-01-01", "params_b": 8},  # newer but smaller
                    "qwen-c": {"released": "2026-02-01", "params_b": 27},
                    "qwen-d": {"released": "2026-03-01", "params_b": 27}}
        lines = timeline(ranked, releases)
        big = lines[("Alibaba (Qwen)", "15B and up")]
        self.assertEqual([e["label"] for e in big], ["a (think)", "c (no-think)", "d (no-think)"])
        self.assertEqual([e["label"] for e in lines[("Alibaba (Qwen)", "under 15B")]], ["b (no-think)"])
        page = timeline_section(ranked, releases, "local")
        self.assertIn("| Alibaba (Qwen) | 15B and up | 2025-01-01 | a (think) | 30B | 50% |", page)
        self.assertEqual(page.count("xychart-beta"), 1)  # only the class with three dates

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

    def test_check_without_memory_keeps_the_earlier_result(self):
        import json
        from omega_transform.modelcheck import ModelCheck, keep_tested
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.json"
            self.assertEqual(keep_tested([], path), ([], []))  # no earlier file
            path.write_text(json.dumps({"models": [
                {"model": "a", "params": "7B", "size_gb": 4.7, "thinking_capable": False,
                 "verdict": "ok", "gen_tokens_per_s": 30.0},
                {"model": "b", "params": "", "size_gb": 9.0, "thinking_capable": False,
                 "verdict": "not enough RAM"}]}))
            now = [ModelCheck("a", "7B", 4.7, False, verdict="not enough RAM"),
                   ModelCheck("b", "", 9.0, False, verdict="not enough RAM"),
                   ModelCheck("c", "", 2.0, False, verdict="broken")]
            checks, kept = keep_tested(now, path)
        self.assertEqual(kept, ["a"])
        self.assertEqual([c.verdict for c in checks], ["ok", "not enough RAM", "broken"])
        self.assertEqual(checks[0].gen_tokens_per_s, 30.0)

    def test_local_and_cloud_models_are_separate_sections(self):
        base = {"example": "toy", "minutes_budget": 5, "initial_score": 1.0, "attempts": 2,
                "ok": 1, "best_score": 2.0}
        page = render([dict(base, model="m:1", provider="ollama", started="1"),
                       dict(base, model="v/x:free", provider="openrouter", started="2",
                            gen_tokens_per_s=150.0, cost_usd=0.0)])
        page = page[page.index("## toy"):]  # the example's own section, after the ranking
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

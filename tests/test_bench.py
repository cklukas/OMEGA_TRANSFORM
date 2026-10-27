# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import json
import math
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
    def test_finished_runs_leave_out_errors(self):
        from omega_transform.bench import finished_runs
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for i, (err, calls) in enumerate([(None, 12), ("RuntimeError: timed out", 0),
                                          (None, 0)]):
            (tmp / f"{i}.json").write_text(json.dumps(
                {"example": f"ex{i}", "model": "m", "variant": "no-think", "error": err,
                 "llm_calls": calls}), encoding="utf-8", newline="\n")
        (tmp / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual(finished_runs(tmp), {("ex0", "m", "no-think")})

    def test_summarize_run(self):
        cfg = load_config(TOY)
        cfg.search.iterations = 10
        tmp = self.enterContext(tempfile.TemporaryDirectory())
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
            f.write_text(json.dumps(old), encoding="utf-8", newline="\n")
            run_dir = run_dir_of(f, runs, old)
            self.assertEqual(run_dir, runs / "20260101-000000" / "toy" / "m_no-think")
            with Engine(cfg, run_dir, llm=ScriptedLLM(incrementer), log=lambda s: None) as eng:
                eng.run()
                best = summarize(eng.db)["best_score"]
            self.assertEqual(add_details(results, runs, log=lambda s: None), [f])
            r = json.loads(f.read_text(encoding="utf-8"))
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
        pages = render(results)
        self.assertEqual(set(pages), {"benchmarks.md", "benchmarks/toy.md",
                                      "benchmarks/developers.md", "benchmarks/countries.md",
                                      "benchmarks/model-check.md"})
        self.assertIn("| [toy](benchmarks/toy.md) | 3 models", pages["benchmarks.md"])
        page = pages["benchmarks/toy.md"]
        self.assertTrue(page.split("-->\n")[-1].lstrip().startswith("# toy\n"))
        self.assertLess(page.index("| b:7b"), page.index("| a:1b"))  # best first
        self.assertIn("xychart-beta", page)
        self.assertIn('x-axis ["b:7b", "a:1b"]', page)
        self.assertIn("failed: model failed to load", page)
        self.assertIn("\n## Local models\n", page)
        self.assertIn("\n### Inference speed and memory\n", page)
        self.assertIn("| a:1b |  | 7.1 | 12 |", page)
        self.assertIn("generation speed (tokens/s)", page)
        self.assertEqual(score_at([[0, 1.0], [6, 2.0]], 5), 1.0)
        self.assertIn("No benchmark results", render([])["benchmarks.md"])

    def test_write_report_removes_stale_sub_pages(self):
        import json
        from omega_transform.report import write_report
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "bench" / "results"
            results.mkdir(parents=True)
            (results / "1.json").write_text(json.dumps(
                {"example": "toy", "model": "m:1", "minutes_budget": 5, "best_score": 2.0,
                 "initial_score": 1.0, "attempts": 1, "ok": 1, "started": "1"}),
                encoding="utf-8", newline="\n")
            out = Path(tmp) / "docs" / "benchmarks.md"
            (out.parent / "benchmarks").mkdir(parents=True)
            (out.parent / "benchmarks" / "gone.md").write_text("old", encoding="utf-8", newline="\n")
            write_report(results, out)
            self.assertTrue(out.exists())
            self.assertEqual(sorted(p.name for p in (out.parent / "benchmarks").iterdir()),
                             ["countries.md", "developers.md", "model-check.md", "toy.md"])

    def test_overall_ranking_weights_every_example_equally(self):
        from omega_transform.report import (countries_page, developers_page, ranking,
                                            ranking_section)
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
        all_runs = [r for rows in by_example.values() for r in rows]
        page = ranking_section(all_runs)
        self.assertIn("## Overall ranking", page)
        self.assertIn("[by developer](benchmarks/developers.md)", page)
        dev_page = developers_page(all_runs)
        self.assertIn("# Models by developer", dev_page)
        self.assertIn("No group has ranked models", countries_page(all_runs))
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
        self.assertIn("## Who develops which model", dev_page)
        self.assertIn("| other | a, b | – |", dev_page)
        from omega_transform.report import DEVELOPERS, DEVELOPER_INFO
        self.assertFalse({who for _, who in DEVELOPERS} - set(DEVELOPER_INFO),
                         "every developer needs a profile")
        two = [dict(run("x", "gemma4:e2b", 1, 2)), dict(run("x", "gpt-oss:20b", 1, 3))]
        g = developers_page(two)
        self.assertIn("- **Google** (founded 1998, Mountain View, USA):", g)
        self.assertNotIn("**Mistral AI**", g)  # only developers with results
        self.assertIn("### Ranking by developer", g)
        self.assertIn("| 2 | China | qwen3:8b | 2025-04-29 | Alibaba (Qwen) | **0%** | −100% | 1 |",
                      countries_page(two + [dict(run("x", "qwen3:8b", 1, 1))],
                                     {"qwen3:8b": {"released": "2025-04-29"}}))
        self.assertIn("| Google | 1 | gemma4:e2b | 2026-04-02 | **50%** |",
                      developers_page(two, {"gemma4:e2b": {"released": "2026-04-02"}}))
        self.assertIn("| 1 | b | – | **83%** |", page)  # release date unknown
        dated = ranking_section(all_runs, {"b": {"released": "2026-01-02"}})
        self.assertIn("| 1 | b | 2026-01-02 | **83%** |", dated)

    def test_machine_key(self):
        from omega_transform.report import machine_key
        self.assertEqual(machine_key({"cpu": "Apple M1 Max", "model": "Mac13,1"}), "Apple M1 Max")
        self.assertEqual(machine_key({"cpu": "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz",
                                      "gpus": ["NVIDIA GeForce GTX 1080 Ti, 11264 MiB"] * 2}),
                         "i7-8700K + 2x GTX 1080 Ti")
        self.assertEqual(machine_key({"cpu": "AMD Ryzen 9 5950X 16-Core Processor",
                                      "gpus": ["NVIDIA GeForce RTX 4090, 24564 MiB"]}),
                         "AMD Ryzen 9 5950X + RTX 4090")
        self.assertEqual(machine_key(None), "unknown machine")

    def test_load_results_from_several_directories(self):
        import json
        from omega_transform.report import current, load_results
        with tempfile.TemporaryDirectory() as tmp:
            mac, win = Path(tmp) / "bench" / "results", Path(tmp) / "win" / "results"
            for d in (mac, win):
                d.mkdir(parents=True)
            run = {"example": "toy", "model": "m", "best_score": 2.0, "initial_score": 1.0}
            for d, name in ((mac, "1_old.json"), (mac, "2_new.json"), (win, "3_win.json"),
                            (win, "2_new.json")):  # the same file copied twice
                (d / name).write_text(json.dumps(run), encoding="utf-8", newline="\n")
            (mac / "notes.json").write_text("[]", encoding="utf-8", newline="\n")
            (mac.parent / "superseded.txt").write_text(
                "# spoiled\n1_old.json  server stalled\n", encoding="utf-8", newline="\n")
            results = load_results([mac, win])
        self.assertEqual([r["_file"] for r in results], ["1_old.json", "2_new.json", "3_win.json"])
        self.assertEqual(results[0]["_superseded"], "server stalled")
        self.assertEqual([r["_file"] for r in current(results)], ["2_new.json", "3_win.json"])

    def test_repeated_runs_mean_and_deviation(self):
        from omega_transform.report import aggregate, mean_sd, run_groups
        self.assertEqual(mean_sd([3]), (3, None))
        m, sd = mean_sd([1.0, 2.0, 3.0])
        self.assertEqual((m, sd), (2.0, 1.0))
        self.assertEqual(mean_sd([]), (None, None))
        mac, win = {"cpu": "Apple M1 Max"}, {"cpu": "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz"}

        def run(best, started, where=mac, **kw):
            return dict(dict(example="toy", model="a", variant="think", initial_score=1.0,
                             best_score=best, improvement=best, started=started, machine=where,
                             llm_calls=10, attempts=10, ok=5, statuses={"ok": 5}), **kw)
        runs = [run(2.0, "1"), run(4.0, "2", llm_calls=20),
                dict(run(1.0, "3"), error="answer stopped: reasoning exceeded"),
                run(9.0, "4", where=win), dict(run(5.0, "0"), _superseded="pilot")]
        groups = run_groups(runs)["toy"]
        self.assertEqual(sorted(groups), [("a (think)", "Apple M1 Max"), ("a (think)", "i7-8700K")])
        row = aggregate(groups[("a (think)", "Apple M1 Max")])
        self.assertEqual((row["runs"], row["failed_runs"]), (3, 1))
        # the failed run counts with its starting program (score 1, gain ×1)
        self.assertAlmostEqual(row["best_score"], (2 + 4 + 1) / 3)
        self.assertAlmostEqual(row["best_score_sd"], mean_sd([2.0, 4.0, 1.0])[1])
        self.assertAlmostEqual(row["improvement"], (2 + 4 + 1) / 3)
        self.assertEqual(row["llm_calls"], 15)  # speed and calls: completed runs only
        self.assertNotIn("error", row)
        single = aggregate(groups[("a (think)", "i7-8700K")])
        self.assertEqual((single["best_score"], single["best_score_sd"], single["llm_calls"]),
                         (9.0, None, 10))
        all_failed = aggregate([dict(run(1.0, "1"), error="boom")])
        self.assertEqual((all_failed["error"], all_failed["failed_runs"]), ("boom", 1))

    def test_example_page_with_repeats_and_machines(self):
        mac = {"cpu": "Apple M1 Max", "model": "Mac13,1", "cores": "10", "memory_gb": 32}
        win = {"cpu": "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz", "cores": "12",
               "memory_gb": 64, "gpus": ["NVIDIA GeForce GTX 1080 Ti, 11264 MiB"] * 2}

        def run(model, best, started, where, **kw):
            return dict(example="toy", model=model, minutes_budget=5, initial_score=1.0,
                        best_score=best, improvement=best, started=started, machine=where,
                        attempts=4, ok=2, llm_calls=4, progress=[[0, 1.0], [2, best]], **kw)
        results = [run("a:1", 2.0, "1", mac), run("a:1", 4.0, "2", mac),
                   run("b:1", 3.0, "3", mac), run("a:1", 1.5, "4", win),
                   run("b:1", 1.0, "5", win, error="answer stopped: reasoning exceeded"),
                   run("b:1", 2.0, "6", win)]
        pages = render(results)
        self.assertIn("| [toy](benchmarks/toy.md) | 2 models in 6 runs", pages["benchmarks.md"])
        page = pages["benchmarks/toy.md"]
        self.assertIn("all runs per model", page)
        self.assertIn("**Apple M1 Max**: Apple M1 Max (Mac13,1), 10 cores, 32 GB, 3 runs", page)
        self.assertIn("Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz, 12 cores", page)
        self.assertIn("2x NVIDIA GeForce GTX 1080 Ti, 11264 MiB", page)
        self.assertLess(page.index("\n## Local models on Apple M1 Max\n"),
                        page.index("\n## Local models on i7-8700K + 2x GTX 1080 Ti\n"))
        self.assertIn("| a:1 | 2 |  | 1 | **3** ± 1.41421 | ×3.00 ± 1.41 | 4 |", page)
        # the failed run counts with its starting program: (1 + 2) / 2
        self.assertIn("| b:1 | 2 (1 failed) |  | 1 | **1.5** ± ", page)
        self.assertIn('title "toy, local, Apple M1 Max: best score (mean; line: + 1 sd)"', page)
        self.assertIn("    bar [3, 3]\n    line [4.414, 3]\n", page)
        # the best score over time is the mean over the runs
        self.assertIn("| a:1 | 1 | 3 | 3 | 3 |", page)
        overview = pages["benchmarks.md"]
        self.assertIn("#### All machines", overview)
        self.assertIn("| On Apple M1 Max | On i7-8700K + 2x GTX 1080 Ti |", overview)
        self.assertIn("#### On i7-8700K + 2x GTX 1080 Ti", overview)

    def test_shares_are_normalised_per_machine(self):
        from omega_transform.report import combined_ranking, ranking

        def run(model, gain, where, example="x", **kw):
            return dict(example=example, model=model, initial_score=10.0,
                        best_score=10.0 * (1 + gain), improvement=1 + gain, started="1",
                        machine={"cpu": where}, **kw)
        # one lucky run of a does not set the bar: the reference is the best mean gain
        mac = {"x": [run("a", 1.0, "M"), run("a", 3.0, "M"), run("b", 1.5, "M"),
                     run("b", 1.5, "M")]}
        ranked = {e["label"]: e for e in ranking(mac)}
        self.assertAlmostEqual(ranked["a"]["share"], 1.0)  # runs at 50% and 150%
        self.assertAlmostEqual(ranked["b"]["share"], 0.75)
        self.assertEqual((ranked["a"]["runs"], ranked["a"]["wins"]), (2, 1))
        self.assertAlmostEqual(ranked["a"]["sd"], math.sqrt(0.5) / math.sqrt(2))
        self.assertAlmostEqual(ranked["b"]["sd"], 0.0)
        # a slow machine's small raw gains are compared with its own best only
        win = {"x": [run("a", 0.2, "W"), run("b", 0.2, "W"),
                     dict(run("b", 0.0, "W"), error="answer stopped: reasoning exceeded")]}
        both = {e["label"]: e for e in combined_ranking({"M": mac, "W": win})}
        self.assertAlmostEqual(both["b"]["by_machine"]["W"], 0.5)  # (100% + 0%) / 2
        self.assertAlmostEqual(both["a"]["by_machine"]["W"], 1.0)
        self.assertAlmostEqual(both["a"]["share"], 1.0)
        self.assertAlmostEqual(both["b"]["share"], (0.75 + 0.5) / 2)
        self.assertEqual((both["b"]["runs"], both["b"]["failed"], both["b"]["cells"]), (4, 1, 2))
        self.assertEqual(both["a"]["examples"], 1)
        self.assertEqual(both["b"]["by_example"], {"x": (0.75 + 0.5) / 2})
        # a model run only once has no deviation
        self.assertIsNone(ranking({"x": [run("a", 1.0, "M"), run("b", 2.0, "M")]})[0]["sd"])

    def test_ties_from_overlapping_ranges(self):
        from omega_transform.report import assign_ranks

        def e(share, sd=None):
            return {"share": share, "sd": sd}
        ranked = assign_ranks([e(0.8, 0.05), e(0.74, 0.02), e(0.7, 0.01), e(0.5), e(0.5),
                               e(0.4)])
        self.assertEqual([(x["rank"], x["tied"]) for x in ranked],
                         [(1, True), (1, True), (3, False), (4, True), (4, True), (6, False)])
        # overlapping only the previous model, not the group's first, starts a new group
        chain = assign_ranks([e(0.8, 0.03), e(0.74, 0.03), e(0.68, 0.03)])
        self.assertEqual([x["rank"] for x in chain], [1, 1, 3])
        from omega_transform.report import ranking_section
        tied = ranking_section([dict(example="x", model=m, initial_score=1.0, best_score=b,
                                     improvement=b, started="1")
                                for m, b in (("a", 3.0), ("b", 2.0), ("c", 2.0))])
        self.assertIn("| 2= | b |", tied)
        self.assertIn("| 2= | c |", tied)

    def test_failed_runs_count_as_no_gain_and_are_shown(self):
        from omega_transform.report import ranking_section
        base = dict(example="x", initial_score=1.0, started="1")
        results = [dict(base, model="a", best_score=3.0, improvement=3.0),
                   dict(base, model="a", best_score=2.0, improvement=2.0),
                   dict(base, model="b", best_score=1.0, improvement=1.0,
                        error="answer stopped: reasoning exceeded"),
                   dict(base, model="b", best_score=3.0, improvement=3.0)]
        page = ranking_section(results)
        # a: runs at 133% and 67% of the best mean gain; b: 0% (failed) and 133%
        self.assertIn("| 1= | a | – | **100%** ± 33% | ×2.45 | 1 | 0 | 2 | 1 |", page)
        self.assertIn("| 1= | b | – | **67%** ± 67% | ×1.73 | 0 | 0 | 2 (1 failed) | 1 |", page)

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
        page = render([], check)["benchmarks/model-check.md"]
        self.assertIn("\n# Model check\n", page)
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
                 "verdict": "not enough RAM"}]}), encoding="utf-8", newline="\n")
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
                            gen_tokens_per_s=150.0, cost_usd=0.0)])["benchmarks/toy.md"]
        local, cloud = page.index("## Local models"), page.index("## Cloud models (OpenRouter)")
        self.assertLess(page.index("| m:1 |"), cloud)
        self.assertGreater(page.index("| v/x:free |"), cloud)
        self.assertIn("toy, cloud: best score", page)
        self.assertIn("### Inference speed and cost", page)
        self.assertLess(local, cloud)

    def test_variants_are_separate_rows(self):
        base = {"example": "toy", "model": "m:1", "minutes_budget": 5, "best_score": 2.0,
                "initial_score": 1.0, "attempts": 1, "ok": 1}
        page = render([dict(base, variant="think", started="1"),
                       dict(base, variant="no-think", started="2", best_score=1.5)])
        page = page["benchmarks/toy.md"]
        self.assertIn("| m:1 (think) |", page)
        self.assertIn("| m:1 (no-think) |", page)


if __name__ == "__main__":
    unittest.main()

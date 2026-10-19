# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from omega_transform.database import Database, Program, new_id
from omega_transform.figures import KINDS, drawable, figure

SOLUTIONS = {
    "circles": {"kind": "circles", "container": "circle",
                "circles": [[0.0, 0.0, 0.5], [0.0, 0.75, 0.25]]},
    "heilbronn": {"kind": "heilbronn", "points": [[0, 0], [1, 0], [0, 1], [1, 1], [0.5, 0.4]]},
    "sphere_points": {"kind": "sphere_points",
                      "points": [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1],
                                 [0, 0, -1]]},
    "tour": {"kind": "tour", "instance": "n4", "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
             "tour": [0, 1, 2, 3]},
    "bins": {"kind": "bins", "capacity": 10, "lower_bound": 2, "bins": [[6, 4], [5], [3, 2]]},
    "cut": {"kind": "cut", "n": 3, "edges": [[0, 1, 2], [1, 2, 5], [0, 2, 1]],
            "side": [0, 1, 0]},
    "regression": {"kind": "regression",
                   "sets": {"test": [[0, 0.1], [1, 0.9], [2, 2.2]], "extra": [[3, 2.5]]}},
    "compression": {"kind": "compression", "input": "log", "head": ["GET / <x> & y"],
                    "lines": 3, "original_bytes": 10000, "compressed_bytes": 2500,
                    "references": {"zlib -9": 3000}},
    "classification": {"kind": "classification", "columns": ["x1", "x2"],
                       "head": [[0.5, -1.25]], "head_labels": [1], "train_rows": 4,
                       "num_features": 3, "test_correct": "1101"},
    "value": {"kind": "value", "value": 40, "limit": 100},
    "matmul": {"kind": "matmul", "n": 64, "cores": "one core"},
    "sort": {"kind": "sort", "n": 1000, "distributions": ["uniform", "dups1000", "sorted",
                                                            "reverse", "nearly_sorted"]},
    "scan": {"kind": "scan", "n": 1000, "head": [5, -3, 256]},
    "parse": {"kind": "parse", "count": 10, "text_mb": 0.5, "head": ["1e23", "4.9e-324"]},
}


class FiguresTest(unittest.TestCase):
    def test_every_kind_draws_valid_deterministic_svg(self):
        self.assertEqual(set(SOLUTIONS), set(KINDS))
        for kind, sol in SOLUTIONS.items():
            with self.subTest(kind=kind):
                self.assertTrue(drawable(sol))
                svg = figure([("Starting program", sol), ("Best", sol)], f"{kind} & co")
                root = ET.fromstring(svg)
                self.assertEqual(root.get("width"), "820")
                self.assertEqual(svg, figure([("Starting program", sol), ("Best", sol)],
                                             f"{kind} & co"))
                self.assertNotIn("nan", svg)

    def test_captions_come_from_the_data(self):
        def caption(sol):
            texts = ET.fromstring(figure([("t", sol)], "x")).iter(
                "{http://www.w3.org/2000/svg}text")
            texts = [t.text for t in texts]
            return texts[texts.index("t") + 1]  # after the panel's title (and any band)
        self.assertEqual(caption(SOLUTIONS["circles"]), "sum of radii 0.75 · 2 circles")
        self.assertEqual(caption(SOLUTIONS["tour"]), "tour length 4 · 4 cities")
        self.assertEqual(caption(SOLUTIONS["bins"]), "3 bins, filled 66.7% on average")
        self.assertTrue(caption(SOLUTIONS["cut"]).startswith("cut 7 of 8 edge weight"))
        self.assertTrue(caption(SOLUTIONS["sphere_points"]).startswith("smallest angle 90°"))
        self.assertEqual(caption(SOLUTIONS["compression"]), "−75.0%: 2.5 KB (ratio 4.00)")
        self.assertEqual(caption(SOLUTIONS["classification"]), "accuracy 75.0% · 3 features")

    def test_speed_kinds_draw_the_recorded_scores(self):
        sol = SOLUTIONS["matmul"]
        svg = figure([("Start", sol, 0.5), ("Best", sol, 2.0)], "matmul")
        self.assertIn("0.5 GFLOP/s · 1.05 ms for one 64×64 product", svg)
        self.assertIn("2 GFLOP/s · 262 µs for one 64×64 product", svg)
        self.assertIn("within 262 µs, the faster program's time: 25% done", svg)
        self.assertIn("C = A · B for 64 × 64 float32 matrices", svg)  # the problem band
        sort = figure([("Start", SOLUTIONS["sort"], 1.0), ("Best", SOLUTIONS["sort"], 4.0)], "s")
        self.assertIn("5 × 1,000 values", sort)

    def test_unknown_or_missing_solutions_are_not_drawable(self):
        for sol in (None, {}, {"kind": "hologram"}, [1, 2]):
            self.assertFalse(drawable(sol))

    def test_database_keeps_solutions_apart_from_programs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "omega.sqlite", islands=1, features=[])
            ok = Program(id=new_id(), code="a", score=1.0, solution=SOLUTIONS["tour"])
            bad = Program(id=new_id(), code="b", status="invalid", solution=SOLUTIONS["tour"])
            huge = Program(id=new_id(), code="c", score=0.5,
                           solution={"kind": "tour", "points": [[math.pi, 1]] * 30000})
            for p in (ok, bad, huge):
                db.add(p)
            self.assertEqual(db.solution(ok.id), SOLUTIONS["tour"])
            self.assertIsNone(db.solution(bad.id))   # only successful programs
            self.assertIsNone(db.solution(huge.id))  # too large to keep
            self.assertIsNone(db.get(ok.id).solution)  # not loaded with the program
            db.close()

    def test_bench_summary_records_initial_and_best_solution(self):
        from omega_transform.bench import summarize
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "omega.sqlite", islands=1, features=[])
            root = Program(id=new_id(), code="a", score=1.0, mode="initial",
                           solution=SOLUTIONS["bins"])
            db.add(root)
            db.add(Program(id=new_id(), code="b", score=2.0, parent_id=root.id,
                           mode="exploit/diff", solution=SOLUTIONS["tour"]))
            s = summarize(db)
            self.assertEqual(s["initial_solution"], SOLUTIONS["bins"])
            self.assertEqual(s["best_solution"], SOLUTIONS["tour"])
            db.close()


class StartPageTest(unittest.TestCase):
    def test_gallery_block_is_renewed_and_links_every_example(self):
        from omega_transform.config import load_config
        from omega_transform.docgen import GALLERY_END, GALLERY_START, start_page
        root = Path(__file__).resolve().parent.parent
        cfg = load_config(root / "examples" / "tsp")
        examples = [("tsp", cfg.category, cfg), ("toy", "Testing", load_config(
            root / "examples" / "toy"))]
        text = f"# Home\n\n{GALLERY_START}\nold\n{GALLERY_END}\n\nmore\n"
        result = {"example": cfg.name, "model": "m", "variant": "think", "best_score": 2,
                  "improvement": 2.0}
        page = start_page(text, [result], examples,
                          {"docs/assets/examples/tsp.svg": "<svg/>"})
        self.assertNotIn("old", page)
        self.assertTrue(page.endswith(f"{GALLERY_END}\n\nmore\n"))
        self.assertIn(f'![tsp](assets/examples/tsp.svg "{cfg.summary}")', page)
        self.assertIn("**[tsp](examples/tsp.md)**", page)
        self.assertNotIn("[toy]", page)  # the test suite's problem stays off the start page
        self.assertIn("1 models and reasoning modes in 1 runs on the 1 examples; on 1 of", page)
        self.assertEqual(start_page("no markers", [result], examples, {}), "no markers")


class ExamplePageFigureTest(unittest.TestCase):
    def test_page_shows_start_and_best_side_by_side(self):
        from omega_transform.config import load_config
        from omega_transform.docgen import example_page, figures
        root = Path(__file__).resolve().parent.parent
        cfg = load_config(root / "examples" / "toy")
        result = {"example": cfg.name, "model": "m", "variant": "think", "best_score": 5,
                  "initial_score": 1, "improvement": 5.0, "minutes_to_best": 2,
                  "minutes_budget": 15, "started": "2026-10-13T00:00:00",
                  "best_lineage": [{}, {}], "best_diff": "--- initial\n+++ best\n",
                  "initial_solution": SOLUTIONS["bins"], "best_solution": SOLUTIONS["bins"]}
        page = example_page("toy", "Testing", cfg, [result])
        self.assertIn("by m (think) produces (right)", page)
        self.assertIn("](../assets/examples/toy.svg)", page)
        svg = figures([result], [("toy", "Testing", cfg)])["docs/assets/examples/toy.svg"]
        self.assertEqual(ET.fromstring(svg).get("width"), "820")
        # no improvement: only the starting program is drawn
        result["best_diff"] = ""
        page = example_page("toy", "Testing", cfg, [result])
        self.assertIn("no model improved on it", page)
        svg = figures([result], [("toy", "Testing", cfg)])["docs/assets/examples/toy.svg"]
        self.assertEqual(ET.fromstring(svg).get("width"), "400")
        # results without solutions: no figure
        del result["initial_solution"]
        self.assertNotIn("assets/examples", example_page("toy", "Testing", cfg, [result]))
        self.assertEqual(figures([result], [("toy", "Testing", cfg)]), {})


if __name__ == "__main__":
    unittest.main()

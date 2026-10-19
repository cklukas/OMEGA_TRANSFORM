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
            return [t.text for t in texts][1]
        self.assertEqual(caption(SOLUTIONS["circles"]), "sum of radii 0.75 · 2 circles")
        self.assertEqual(caption(SOLUTIONS["tour"]), "tour length 4 · 4 cities")
        self.assertEqual(caption(SOLUTIONS["bins"]), "3 bins, filled 66.7% on average")
        self.assertTrue(caption(SOLUTIONS["cut"]).startswith("cut 7 of 8 edge weight"))
        self.assertTrue(caption(SOLUTIONS["sphere_points"]).startswith("smallest angle 90°"))

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

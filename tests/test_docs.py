# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest
from pathlib import Path

from omega_transform.docgen import pages

ROOT = Path(__file__).resolve().parent.parent


class DocsTest(unittest.TestCase):
    def test_generated_pages_are_current(self):
        for rel, text in pages().items():
            with self.subTest(page=rel):
                self.assertEqual((ROOT / rel).read_text(), text,
                                 f"{rel} is out of date: run `python3 -m omega_transform docs`")

    def test_every_example_page_is_in_the_navigation(self):
        nav = (ROOT / "ckdocs.yml").read_text()
        for rel in pages():
            if rel.startswith("docs/examples/"):
                self.assertIn(f"- {rel}\n", nav, f"add {rel} to the nav in ckdocs.yml")

    def test_every_benchmark_page_is_in_the_navigation(self):
        import json
        from omega_transform.report import load_results, render
        nav = (ROOT / "ckdocs.yml").read_text()
        check = json.loads((ROOT / "bench" / "model-check.json").read_text())
        for rel in render(load_results(ROOT / "bench" / "results"), check):
            with self.subTest(page=rel):
                self.assertRegex(nav, rf"- (page: )?docs/{rel}\n",
                                 f"add docs/{rel} to the nav in ckdocs.yml")

    def test_example_page(self):
        from omega_transform.config import load_config
        from omega_transform.docgen import example_page
        cfg = load_config(ROOT / "examples" / "toy")
        self.assertIn("has not been benchmarked yet", example_page("toy", "Testing", cfg, []))
        steps = [{"minutes": 0, "score": 1, "mode": "initial", "status": "ok",
                  "idea": "initial program"},
                 {"minutes": 1, "score": None, "mode": "explore/rewrite/x", "status": "invalid",
                  "idea": "a | broken idea"},
                 {"minutes": 2, "score": 5, "mode": "sweep/explore/diff/x", "status": "ok",
                  "idea": "tuned K=5"}]
        result = {"example": cfg.name, "model": "m", "variant": "no-think", "best_score": 5,
                  "initial_score": 1, "improvement": 5.0, "minutes_to_best": 2,
                  "minutes_budget": 15, "started": "2026-10-13T00:00:00",
                  "best_lineage": steps, "best_diff": "--- initial\n+++ best\n+```\n"}
        page = example_page("toy", "Testing", cfg, [result])
        self.assertIn("**m (no-think)**", page)
        self.assertIn("| 1 | 1.0 | invalid | rewrite | a / broken idea |", page)
        self.assertIn("| 2 | 2.0 | 5 | parameter sweep | tuned K=5 |", page)
        self.assertIn("````diff\n", page)  # a fence longer than the backticks inside

    def test_every_setting_is_described(self):
        page = pages()["docs/configuration.md"]
        for line in page.splitlines():
            if line.startswith("| `"):
                self.assertFalse(line.rstrip().endswith("|  |"), f"undocumented: {line}")


if __name__ == "__main__":
    unittest.main()

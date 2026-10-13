# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest

from omega_transform.landscape import SnapshotError, parse_rankings, render

PAGE = ('...\\"queries\\":[{\\"state\\":{\\"data\\":[{\\"date\\":\\"2026-10-09 00:00:00\\",'
        '\\"model_permaslug\\":\\"vendor/fast-20261001\\",\\"variant\\":\\"standard\\",'
        '\\"total_completion_tokens\\":1000,\\"total_prompt_tokens\\":9000,'
        '\\"total_native_tokens_reasoning\\":0,\\"count\\":42}]}}]...')


def model(mid, tokens, eff, listed=True, variant="standard"):
    return {"id": mid, "variant": variant, "date": "2026-10-09", "tokens": tokens,
            "prompt_tokens": tokens * 0.9, "completion_tokens": tokens * 0.1,
            "requests": 1e6, "price_in": eff, "price_out": eff * 4, "effective_price": eff,
            "free_variant": False, "context": 262144, "reasoning": True, "listed": listed}


class LandscapeTest(unittest.TestCase):
    def test_parse_embedded_rankings(self):
        rows = parse_rankings(PAGE)
        self.assertEqual(rows, [{"date": "2026-10-09", "slug": "vendor/fast-20261001",
                                 "variant": "standard", "completion_tokens": 1000,
                                 "prompt_tokens": 9000, "requests": 42}])
        with self.assertRaises(SnapshotError):
            parse_rankings("<html>no data</html>")

    def test_render_table_and_charts(self):
        snap = {"taken": "2026-10-10T08:00:00", "models": [
            model("a/cheap-popular", 30e12, 0.1), model("b/pricey", 2e12, 4.0),
            model("c/hidden", 5e12, 0, listed=False),
            model("d/free:free", 4e12, 0.0, variant="free")]}
        page = render(snap, benchmarked={"b/pricey"})
        self.assertIn("| 1 | a/cheap-popular | 30,000 |", page)
        self.assertIn("b/pricey ★", page)
        self.assertIn("c/hidden (unlisted) | 5,000 | 1.0 | - | - | - |", page)
        self.assertIn("quadrantChart", page)
        self.assertIn("cheap-popular: [0.050, 0.950]", page)
        self.assertIn("pricey: [0.950, 0.050]", page)
        self.assertNotIn("free: [", page)  # free and unlisted models stay off the cost chart


if __name__ == "__main__":
    unittest.main()

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest

from omega_transform.landscape import (MONTH_PER_WEEK, SnapshotError, cache_share,
                                       parse_rankings, parse_weighted_prices, render, revenue,
                                       vendor)

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
        self.assertIn("pricey: [0.950, 0.050] radius: 22.0", page)  # earns the most

    def test_revenue_uses_the_paid_input_price(self):
        m = dict(model("a/x", 1e9, 1.0), price_in_paid=0.25, price_cache_read=0.1)
        # 0.9e9 input tokens at $0.25/M plus 0.1e9 output tokens at $4/M
        self.assertAlmostEqual(revenue(m), 225 + 400)
        self.assertAlmostEqual(revenue(dict(m, price_in_paid=None)), 900 + 400)
        self.assertIsNone(revenue(dict(m, listed=False)))
        self.assertAlmostEqual(cache_share(m), 0.75 / 0.9)
        self.assertIsNone(cache_share(dict(m, price_in_paid=2.0)))  # price changed
        self.assertEqual(vendor("anthropic/claude"), ("Anthropic", "USA"))
        self.assertEqual(vendor("newco/model"), ("newco", "unknown"))
        self.assertEqual(parse_weighted_prices('x\\"weightedInputPrices\\":{\\"a/x-1\\":0.5}'),
                         {"a/x-1": 0.5})
        self.assertEqual(parse_weighted_prices("nothing"), {})

    def test_revenue_tables_and_price_history(self):
        old = {"taken": "2026-10-01T08:00:00", "models": [model("a/cheap-popular", 30e12, 0.2)]}
        snap = {"taken": "2026-10-10T08:00:00", "models": [
            model("a/cheap-popular", 30e12, 0.1), model("b/pricey", 2e12, 4.0)],
            "prices": {"a/cheap-popular": [0.1, 0.4, None], "b/pricey": [4.0, 16.0, 0.4]}}
        page = render(snap, history=[old, snap])
        # a: 27e12 * 0.1 + 3e12 * 0.4 = $3.9M a week; b: 1.8e12 * 4 + 0.2e12 * 16 = $10.4M
        self.assertIn("| 1 | b/pricey | b | unknown |", page)
        self.assertIn(f"${10.4 * MONTH_PER_WEEK:,.1f}M | 72.7% |", page)
        self.assertIn("### Revenue by vendor", page)
        self.assertIn("### Revenue by country", page)
        self.assertIn("| a/cheap-popular | 0.2 / 0.8 | 0.1 / 0.4 | -50% |", page)
        self.assertIn("| b/pricey | - | 4 / 16 | - |", page)
        self.assertIn('x-axis ["2026-10-01", "2026-10-10"]', page)
        self.assertIn("line [0.26, 0.13]", page)


if __name__ == "__main__":
    unittest.main()

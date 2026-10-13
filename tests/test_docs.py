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

    def test_every_setting_is_described(self):
        page = pages()["docs/configuration.md"]
        for line in page.splitlines():
            if line.startswith("| `"):
                self.assertFalse(line.rstrip().endswith("|  |"), f"undocumented: {line}")


if __name__ == "__main__":
    unittest.main()

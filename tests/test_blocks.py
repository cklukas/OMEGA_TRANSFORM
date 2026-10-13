# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest

from omega_transform.blocks import (EditError, ParsedResponse, apply_answer, apply_edits,
                                    apply_rewrite, editable_blocks, parse_response)

PY = """import math

# OMEGA-BLOCK-START
def f(x):
    y = x + 1
    return y
# OMEGA-BLOCK-END

def run():
    return f(1)
"""

CPP = """#include <cstdio>
// OMEGA-BLOCK-START
int f(int x) { return x; }
// OMEGA-BLOCK-END
int main() { std::printf("%d", f(1)); }
"""


class BlocksTest(unittest.TestCase):
    def test_blocks(self):
        self.assertEqual(editable_blocks(PY), ["def f(x):\n    y = x + 1\n    return y\n"])
        self.assertEqual(editable_blocks(CPP), ["int f(int x) { return x; }\n"])

    def test_exact_edit(self):
        new = apply_edits(PY, [("    y = x + 1", "    y = x + 2")])
        self.assertIn("y = x + 2", new)

    def test_fuzzy_edit_reindents(self):
        new = apply_edits(PY, [("y = x + 1\nreturn y", "z = x * 3\nreturn z")])
        self.assertIn("    z = x * 3\n    return z\n", new)

    def test_fuzzy_ignores_blank_lines(self):
        code = "# OMEGA-BLOCK-START\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n# OMEGA-BLOCK-END\n"
        new = apply_edits(code, [("def a():\n    return 1\n\ndef b():", "def a():\n    return 3\n\n\ndef b():")])
        self.assertIn("return 3\n\n\ndef b():\n    return 2", new)

    def test_salvage_whole_region_edit(self):
        # SEARCH reaches into the fixed header; REPLACE carries the whole region.
        search = "import math\n\n# OMEGA-BLOCK-START\ndef f(x):"
        replace = "import numpy\n# OMEGA-BLOCK-START\ndef f(x):\n    return 7\n# OMEGA-BLOCK-END"
        new = apply_answer(PY, ParsedResponse(edits=[(search, replace)]))
        self.assertIn("    return 7", new)
        self.assertTrue(new.startswith("import math"))

    def test_salvage_restated_edits(self):
        e1 = ("    y = x + 1\n    return y", "    y = x + 5\n    return y")
        e2 = ("    y = x + 1\n    return y", "    y = x + 9\n    return y")
        new = apply_answer(PY, ParsedResponse(edits=[e1, e2]))
        self.assertIn("x + 9", new)

    def test_rewrite_missing_end_marker(self):
        payload = CPP.replace("// OMEGA-BLOCK-END\n", "").replace("return x;", "return 4 * x;")
        new = apply_rewrite(CPP, payload)
        self.assertIn("4 * x", new)
        self.assertEqual(new.count("int main()"), 1)

    def test_edit_outside_block_rejected(self):
        with self.assertRaises(EditError):
            apply_edits(PY, [("    return f(1)", "    return 99")])

    def test_missing_search(self):
        with self.assertRaises(EditError) as cm:
            apply_edits(PY, [("nonexistent line", "x")])
        self.assertIn("not found", str(cm.exception))

    def test_rewrite_body_and_full_file(self):
        new = apply_rewrite(CPP, "int f(int x) { return 2 * x; }")
        self.assertIn("2 * x", new)
        self.assertTrue(new.startswith("#include"))
        full = CPP.replace("return x;", "return 3 * x;")
        self.assertIn("3 * x", apply_rewrite(CPP, full))

    def test_parse(self):
        text = """<think>hmm</think>
**IDEA:** double the increment
<<<<<<< SEARCH
    y = x + 1
=======
    y = x + 2
>>>>>>> REPLACE
"""
        p = parse_response(text)
        self.assertEqual(p.idea, "double the increment")
        self.assertEqual(p.edits, [("    y = x + 1", "    y = x + 2")])
        p = parse_response("IDEA: new\n```python\ndef f(x):\n    return 5\n```\n")
        self.assertEqual(p.code, "def f(x):\n    return 5\n")

    def test_empty_replace(self):
        p = parse_response("IDEA: x\n<<<<<<< SEARCH\n    y = x + 1\n=======\n>>>>>>> REPLACE")
        self.assertEqual(p.edits, [("    y = x + 1", "")])


if __name__ == "__main__":
    unittest.main()

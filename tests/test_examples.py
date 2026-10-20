# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from omega_transform.config import load_config
from omega_transform.evaluator import Evaluator

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted(p.parent for p in (ROOT / "examples").glob("*/omega.toml"))


class ExamplesTest(unittest.TestCase):
    def test_every_example_is_configured(self):
        self.assertGreaterEqual(len(EXAMPLES), 4)
        for ex in EXAMPLES:
            with self.subTest(example=ex.name):
                cfg = load_config(ex)
                self.assertTrue(cfg.initial_path.exists())
                self.assertTrue(cfg.category, "set [problem] category for the gallery")
                self.assertTrue(cfg.description.strip())

    # Slow (builds and benchmarks every example), so local runs skip it by default;
    # CI always sets OMEGA_EXAMPLE_TESTS=1 (.ckgit/ci.yml).
    @unittest.skipUnless(os.environ.get("OMEGA_EXAMPLE_TESTS"),
                         "set OMEGA_EXAMPLE_TESTS=1 to evaluate every example (slow; CI does)")
    def test_every_starting_program_passes_its_evaluator(self):
        for ex in EXAMPLES:
            cfg = load_config(ex)
            if cfg.evaluator.build and not shutil.which(cfg.evaluator.build[0]):
                continue  # no compiler for this example here
            with self.subTest(example=ex.name), tempfile.TemporaryDirectory() as tmp:
                code = cfg.initial_path.read_text(encoding="utf-8")
                res = Evaluator(cfg, Path(tmp)).evaluate(code, "t")
                self.assertEqual(res.status, "ok", f"{ex.name}: {res.feedback}")
                if res.solution is not None:  # the example page draws it
                    from omega_transform.figures import drawable, figure
                    self.assertTrue(drawable(res.solution), f"{ex.name}: unknown solution kind")
                    self.assertIn("<svg", figure([("start", res.solution)], ex.name))


    @unittest.skipUnless(os.environ.get("OMEGA_EXAMPLE_TESTS"),
                         "set OMEGA_EXAMPLE_TESTS=1 to evaluate every example (slow; CI does)")
    def test_no_python_candidate_can_read_its_evaluator(self):
        from omega_transform.evaluator import sandbox_available
        if not sandbox_available():
            self.skipTest("read protection needs a sandbox (sandbox-exec, bwrap or an AppContainer)")
        probe = ('import os as _os\n'
                 '_os.read(_os.open(_os.environ["OMEGA_PROBLEM_DIR"] + "/evaluate.py", 0), 9)\n')
        for ex in EXAMPLES:
            cfg = load_config(ex)
            if cfg.lang_tag != "python":
                continue
            code = cfg.initial_path.read_text(encoding="utf-8")
            marker = next(ln for ln in code.splitlines(keepends=True) if "OMEGA-BLOCK-START" in ln)
            cheat = code.replace(marker, marker + probe, 1)
            with self.subTest(example=ex.name), tempfile.TemporaryDirectory() as tmp:
                res = Evaluator(cfg, Path(tmp)).evaluate(cheat, "cheat")
                self.assertNotEqual(res.status, "ok", f"{ex.name} read its evaluator")
                self.assertTrue(any(m in res.feedback for m in (  # macOS, Windows refuse; Linux hides
                    "Operation not permitted", "Permission denied", "No such file or directory")),
                    res.feedback)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import tempfile
import textwrap
import unittest
from pathlib import Path

from omega_transform.config import load_config
from omega_transform.evaluator import Evaluator, sandbox_available

TOY = Path(__file__).resolve().parent.parent / "examples" / "toy"


def program(body: str) -> str:
    return "# OMEGA-BLOCK-START\ndef value():\n" + textwrap.indent(body, "    ") + \
        "\n# OMEGA-BLOCK-END\n"


@unittest.skipUnless(sandbox_available(), "no sandbox here (macOS sandbox-exec, Linux bwrap or Windows AppContainers)")
class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(TOY)
        self.cfg.evaluator.sandbox = True
        self.tmp = tempfile.TemporaryDirectory()
        self.ev = Evaluator(self.cfg, Path(self.tmp.name) / "jobs")
        self.outside = Path(self.tmp.name) / "outside.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_job_dir_writable(self):
        r = self.ev.evaluate(program(
            "import os\nopen(os.environ['OMEGA_WORKDIR'] + '/x', 'w').write('ok')\nreturn 3"), "w")
        self.assertEqual((r.status, r.score), ("ok", 3))

    def test_temporary_files_go_to_the_job_dir(self):
        # On Windows, a container's TEMP would otherwise be its profile folder.
        r = self.ev.evaluate(program(
            "import os, tempfile\nreturn 3 if os.path.realpath(tempfile.gettempdir()) == "
            "os.path.realpath(os.environ['OMEGA_WORKDIR']) else 0"), "t")
        self.assertEqual((r.status, r.score), ("ok", 3))

    def test_write_outside_job_dir_denied(self):
        r = self.ev.evaluate(program(f"open({str(self.outside)!r}, 'w').write('x')\nreturn 1"), "o")
        self.assertNotEqual(r.status, "ok")
        self.assertFalse(self.outside.exists())

    def test_network_denied(self):
        r = self.ev.evaluate(program(
            "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=2)\nreturn 1"), "n")
        self.assertNotEqual(r.status, "ok")
        # macOS refuses the connection; Linux has no route in its empty network namespace;
        # Windows refuses the socket access (WSAEACCES) to a container without capabilities.
        self.assertTrue(any(m in r.feedback for m in ("Operation not permitted",
                                                       "Network is unreachable",
                                                       "WinError 10013")), r.feedback)


class UsageTest(unittest.TestCase):
    def test_cpu_time_and_peak_memory_are_recorded(self):
        cfg = load_config(TOY)
        with tempfile.TemporaryDirectory() as tmp:
            r = Evaluator(cfg, Path(tmp)).evaluate(program(
                "x = 0\nfor i in range(3_000_000):\n    x += i\n"
                "buf = bytearray(64 * 2**20)\nreturn 5"), "u")
        self.assertEqual(r.status, "ok")
        m = r.usage_metrics()
        self.assertGreater(m["main_cpu_s"], 0.05)
        self.assertGreater(m["main_peak_mb"], 60)


class LimitsTest(unittest.TestCase):
    def test_memory_limit_kills_job(self):
        cfg = load_config(TOY)
        cfg.evaluator.max_memory_mb = 200
        with tempfile.TemporaryDirectory() as tmp:
            ev = Evaluator(cfg, Path(tmp))
            r = ev.evaluate(program(
                "import time\nblocks = []\nfor _ in range(60):\n"
                "    blocks.append(bytearray(20 * 2**20))\n    time.sleep(0.05)\nreturn 1"), "m")
            self.assertEqual(r.status, "runtime_error")
            self.assertIn("memory", r.feedback)

    def test_timeout(self):
        cfg = load_config(TOY)
        cfg.evaluator.stages[0].timeout = 1
        with tempfile.TemporaryDirectory() as tmp:
            r = Evaluator(cfg, Path(tmp)).evaluate(program("import time\ntime.sleep(30)\nreturn 1"), "t")
            self.assertEqual(r.status, "timeout")
            self.assertLess(r.seconds, 5)


if __name__ == "__main__":
    unittest.main()


class IsolationTest(unittest.TestCase):
    """Python candidates run in their own process that cannot read the problem directory."""

    def evaluate(self, body: str):
        cfg = load_config(TOY)
        with tempfile.TemporaryDirectory() as tmp:
            return Evaluator(cfg, Path(tmp) / "jobs").evaluate(program(body), "iso")

    def test_candidate_runs_isolated(self):
        r = self.evaluate("return 7")
        self.assertEqual((r.status, r.score), ("ok", 7))

    @unittest.skipUnless(sandbox_available(), "no sandbox here (macOS sandbox-exec, Linux bwrap or Windows AppContainers)")
    def test_candidate_cannot_read_the_evaluator(self):
        evaluator = str(TOY / "evaluate.py")
        r = self.evaluate(f"open({evaluator!r}).read()\nreturn 1")
        self.assertEqual(r.status, "invalid")
        # macOS and Windows refuse the read; Linux hides the problem directory altogether.
        self.assertTrue(any(m in r.feedback for m in ("Operation not permitted",
                                                       "Permission denied",
                                                       "No such file or directory")), r.feedback)

    def test_a_crash_in_the_candidate_is_feedback(self):
        r = self.evaluate("return 1 / 0")
        self.assertEqual(r.status, "invalid")
        self.assertIn("ZeroDivisionError", r.feedback)

    def test_work_at_load_time_counts_as_cpu(self):
        import os
        from omega_transform.harness import Candidate
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("x = sum(range(2_000_000))\ndef run():\n    return x\n")
        os.environ.pop("OMEGA_RUNNER", None)
        cand = Candidate(f.name)
        cand.call("run")
        self.assertGreater(cand.last_cpu, 0.01)
        os.unlink(f.name)

    def test_harness_passes_bytes_and_measures_cpu(self):
        import os
        from omega_transform.harness import Candidate
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            # Enough work to register on Windows, whose process clock ticks in 15.6 ms steps.
            f.write("def echo(b):\n    x = 0\n    for i in range(3000000):\n        x += i\n"
                    "    return b[::-1], (1, 2)\n")
        os.environ.pop("OMEGA_RUNNER", None)
        cand = Candidate(f.name)
        self.assertEqual(cand.call("echo", b"abc"), [b"cba", [1, 2]])
        self.assertGreater(cand.last_cpu, 0)
        os.unlink(f.name)

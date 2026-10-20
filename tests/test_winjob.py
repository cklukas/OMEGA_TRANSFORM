# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Windows process control (Job Objects, named pipes): what process groups,
getrusage and FIFOs do on macOS and Linux."""
import subprocess
import sys
import threading
import time
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class JobTest(unittest.TestCase):
    def test_terminate_kills_the_whole_tree(self):
        from omega_transform.winjob import Job
        child = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', " \
                "'import time; time.sleep(60)']); time.sleep(60)"
        job = Job()
        proc = subprocess.Popen([sys.executable, "-c", child])
        job.assign(proc)
        time.sleep(1.0)  # the grandchild has started and joined the job
        t0 = time.monotonic()
        job.terminate()
        proc.wait(timeout=10)
        self.assertLess(time.monotonic() - t0, 5)
        job.close()

    def test_usage_covers_the_tree(self):
        from omega_transform.winjob import run_measured
        work = "x = 0\nfor i in range(5_000_000):\n    x += i\n"
        done, cpu, user, peak = run_measured(
            [sys.executable, "-c", f"import subprocess, sys; subprocess.run([sys.executable, "
                                   f"'-c', {work!r}])"], timeout=60)
        self.assertEqual(done.returncode, 0)
        self.assertGreater(cpu, 0.1)  # the grandchild's loop counts
        self.assertGreaterEqual(cpu, user)
        self.assertGreater(peak, 1 << 20)

    def test_timeout(self):
        from omega_transform.winjob import run_measured
        done, *_ = run_measured([sys.executable, "-c", "import time; time.sleep(30)"],
                                timeout=0.5)
        self.assertIsNone(done)

    def test_named_pipe_round_trip(self):
        from omega_transform.winjob import open_pipe, pipe_name, serve_pipe
        name = pipe_name("test")
        got = []

        def server():
            with serve_pipe(name, inbound=True) as f:
                got.append(f.readline())

        t = threading.Thread(target=server)
        t.start()
        with open_pipe(name, "w") as f:
            f.write("héllo ★\n")
        t.join(10)
        self.assertEqual(got, ["héllo ★\n"])


if __name__ == "__main__":
    unittest.main()

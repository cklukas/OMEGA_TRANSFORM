# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""A busy Ollama server gets time before a run counts it unreachable (llm.ollama_get)."""
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from omega_transform import llm
from omega_transform.config import LLMConfig, ModelSpec


class FakeClock:
    """Stands in for llm.time: sleeping advances the clock instead of waiting."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BusyOllama:
    """A local server that answers HTTP 503 to the first ``busy`` requests, then
    lists one model, as /api/tags does."""

    def __init__(self, busy: int):
        self.busy, self.requests = busy, 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                if outer.requests <= outer.busy:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = json.dumps({"models": [{"name": "qwen3.5:latest", "size": 1}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OllamaGetTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = mock.patch.object(llm, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_busy_server_gets_time(self):
        server = BusyOllama(busy=3)
        self.addCleanup(server.close)
        tags = llm.ollama_get(server.base, "/api/tags")
        self.assertEqual(tags["models"][0]["name"], "qwen3.5:latest")
        self.assertEqual(server.requests, 4)

    def test_a_server_that_stays_busy_fails_after_the_wait(self):
        server = BusyOllama(busy=10**9)
        self.addCleanup(server.close)
        with self.assertRaises(OSError):
            llm.ollama_get(server.base, "/api/tags", wait=120)
        self.assertLessEqual(self.clock.now, 120)
        self.assertGreater(self.clock.now, 60)  # it did keep trying

    def test_a_server_that_is_not_running_fails_quickly(self):
        with self.assertRaises(OSError):
            llm.ollama_get(f"http://127.0.0.1:{closed_port()}", "/api/tags")
        self.assertLessEqual(self.clock.now, llm.OLLAMA_DOWN_WAIT)

    def test_check_waits_for_a_busy_server_and_reports_one_that_is_down(self):
        server = BusyOllama(busy=2)
        self.addCleanup(server.close)
        ok = llm.Ensemble(LLMConfig(models=[ModelSpec("qwen3.5:latest")], base_url=server.base))
        with mock.patch.object(llm.Ensemble, "_memory_problems", return_value=[]):
            self.assertEqual(ok.check(), [])
        down = llm.Ensemble(LLMConfig(models=[ModelSpec("qwen3.5:latest")],
                                      base_url=f"http://127.0.0.1:{closed_port()}"))
        problems = down.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot reach ollama", problems[0])


if __name__ == "__main__":
    unittest.main()

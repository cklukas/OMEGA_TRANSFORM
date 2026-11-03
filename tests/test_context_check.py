# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""A local run starts only with the full context window (bench.load_with_context)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from omega_transform import bench


class ShortOllama:
    """A local server that loads the model with ``contexts[i]`` tokens on the i-th
    load (the last one after that) and reports it in /api/ps, as Ollama does."""

    def __init__(self, contexts: list[int], trained: int = 262144):
        self.contexts, self.loads, self.unloads, self.loaded = contexts, 0, 0, None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # /api/ps
                models = [] if outer.loaded is None else [
                    {"name": "m:1", "size": 1, "size_vram": 1, "context_length": outer.loaded}]
                self._send({"models": models})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    self._send({"model_info": {"arch.context_length": trained}})
                elif req.get("keep_alive") == 0:
                    outer.unloads += 1
                    outer.loaded = None
                    self._send({})
                else:
                    outer.loaded = outer.contexts[min(outer.loads, len(outer.contexts) - 1)]
                    outer.loads += 1
                    self._send({"done": True})

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ContextCheckTest(unittest.TestCase):
    def load(self, contexts, trained=262144):
        server = ShortOllama(contexts, trained)
        self.addCleanup(server.close)
        lines = []
        with mock.patch.object(bench, "unload",
                               side_effect=lambda base, name: setattr(server, "loaded", None)):
            got = bench.load_with_context(server.base, "m:1", 40960, "30m", log=lines.append,
                                          pause=0)
        return got, server, lines

    def test_full_context_at_once(self):
        got, server, lines = self.load([40960])
        self.assertEqual((got, server.loads, lines), (40960, 1, []))

    def test_retries_until_the_full_context(self):
        got, server, lines = self.load([32768, 40960])
        self.assertEqual((got, server.loads), (40960, 2))
        self.assertIn("context of 32768 tokens instead of 40960", lines[0])

    def test_a_model_trained_for_less_is_fine(self):
        got, server, lines = self.load([32768], trained=32768)
        self.assertEqual((got, server.loads, lines), (32768, 1, []))

    def test_fails_when_it_stays_short(self):
        with self.assertRaisesRegex(RuntimeError, "32768 tokens instead of 40960"):
            self.load([32768])


if __name__ == "__main__":
    unittest.main()

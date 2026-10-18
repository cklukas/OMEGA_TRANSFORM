# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import json
import os
import random
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from omega_transform.config import LLMConfig, ModelSpec
from omega_transform.llm import Ensemble, LLMError, remote_models

REQUESTS: list[dict] = []


class FakeOpenRouter(BaseHTTPRequestHandler):
    """Streams an answer the way OpenRouter does: keep-alive comments, reasoning
    deltas, content deltas, then usage with the request's cost."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"body": body, "auth": self.headers.get("Authorization"),
                         "path": self.path})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [": OPENROUTER PROCESSING",
                  {"choices": [{"delta": {"reasoning": "thinking about it"}}]},
                  {"choices": [{"delta": {"content": "IDEA: return 2\n"}}]},
                  {"choices": [{"delta": {"content": "done"}}]},
                  {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30,
                                            "cost": 0.0042}},
                  "data: [DONE]"]
        for ev in events:
            line = ev if isinstance(ev, str) else "data: " + json.dumps(ev)
            self.wfile.write((line + "\n\n").encode())
            self.wfile.flush()

    def log_message(self, *args):
        pass


class OpenRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/api/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def ensemble(self, **kw) -> Ensemble:
        cfg = LLMConfig(models=[ModelSpec("vendor/model", provider="openrouter",
                                          base_url=self.url)], **kw)
        return Ensemble(cfg)

    def test_streams_answer_and_records_cost(self):
        REQUESTS.clear()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}):
            llm = self.ensemble(think=True)
            text, model = llm.complete([{"role": "user", "content": "hi"}], random.Random(0))
        self.assertEqual((text, model), ("IDEA: return 2\ndone", "vendor/model"))
        st = llm.last_stats
        self.assertEqual((st.provider, st.prompt_tokens, st.gen_tokens), ("openrouter", 120, 30))
        self.assertAlmostEqual(st.cost_usd, 0.0042)
        self.assertAlmostEqual(llm.cost_usd, 0.0042)
        req = REQUESTS[-1]
        self.assertEqual(req["auth"], "Bearer sk-or-test")
        self.assertEqual(req["path"], "/api/v1/chat/completions")
        self.assertEqual(req["body"]["reasoning"], {"enabled": True})
        self.assertEqual(req["body"]["usage"], {"include": True})

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = self.ensemble(openrouter_key_file="")
            with self.assertRaises(LLMError) as cm:
                llm.complete([{"role": "user", "content": "hi"}], random.Random(0))
        self.assertIn("no OpenRouter key", str(cm.exception))

    def test_failed_request_records_its_duration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = self.ensemble(openrouter_key_file="")
            with self.assertRaises(LLMError):
                llm.complete([{"role": "user", "content": "hi"}], random.Random(0))
        self.assertGreater(llm.last_stats.total_seconds, 0)

    def test_key_file_is_read_when_no_variable(self):
        import tempfile
        from omega_transform.llm import openrouter_key
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("my key: sk-or-v1-abc123\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(openrouter_key(LLMConfig(openrouter_key_file=f.name)),
                             "sk-or-v1-abc123")
        os.unlink(f.name)

    def test_remote_models_are_named(self):
        cfg = LLMConfig(models=[ModelSpec("qwen3.5:latest"),
                                ModelSpec("vendor/model", provider="openrouter")])
        self.assertEqual(remote_models(cfg), ["vendor/model"])


if __name__ == "__main__":
    unittest.main()


class ByteTokenTest(unittest.TestCase):
    def test_byte_fallback_tokens_become_characters(self):
        from omega_transform.llm import decode_byte_tokens
        # LLM-jp-4 on Ollama: every line break arrived as "<0x0A>"
        self.assertEqual(decode_byte_tokens("<<<<<<< SEARCH<0x0A>x = 1<0x0A>"),
                         "<<<<<<< SEARCH\nx = 1\n")
        self.assertEqual(decode_byte_tokens("<0xE3><0x81><0x82>"), "あ")  # multi-byte UTF-8
        for text in ("plain text", 'x = "<0x0"', "<0xZZ>"):
            self.assertEqual(decode_byte_tokens(text), text)

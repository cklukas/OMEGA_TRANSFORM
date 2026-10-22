# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""LLM access: Ollama's native API or any OpenAI-compatible server
(llama.cpp server, vLLM, LM Studio), plus a weighted model ensemble.

Standard library only (urllib), so the engine has no runtime dependencies.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
import os
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from .config import LLMConfig

Messages = list[dict[str, str]]


class LLMError(Exception):
    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial  # what arrived before a runaway answer was stopped


class LLM(Protocol):
    def complete(self, messages: Messages, rng: random.Random,
                 model: str | None = None, max_chars: int = 0) -> tuple[str, str]:
        """Return (response text, model name used)."""
        ...


def _is_timeout(e: BaseException) -> bool:
    reason = getattr(e, "reason", None)
    return isinstance(e, TimeoutError) or isinstance(reason, TimeoutError) or \
        "timed out" in str(e).lower()


class Runaway(Exception):
    """The answer grew past its budget or started repeating itself."""

    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial


def looks_repetitive(text: str, window: int = 300, repeats: int = 3) -> bool:
    """True when the last ``window`` characters already occurred ``repeats`` times."""
    if len(text) < window * (repeats + 1):
        return False
    tail = text[-window:]
    return text.count(tail) >= repeats


_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
             "/opt/homebrew/etc/openssl@3/cert.pem", "/usr/local/etc/openssl@3/cert.pem")
_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that also works with Python builds that ship without
    CA certificates (the python.org macOS installer): fall back to the system bundle."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        if not ctx.cert_store_stats().get("x509_ca"):
            for path in _CA_FILES:
                if os.path.exists(path):
                    ctx.load_verify_locations(cafile=path)
                    break
        _ssl_ctx = ctx
    return _ssl_ctx


_BYTE_TOKENS = re.compile(r"(?:<0x[0-9A-Fa-f]{2}>)+")


def decode_byte_tokens(text: str) -> str:
    """Turn runs of byte-fallback tokens ("<0x0A>", "<0xE3><0x81><0x82>") back into
    the characters they encode. Some GGUF conversions on Ollama hand these tokens
    out literally instead of decoding them, so every line break of an answer
    arrives as "<0x0A>" and no edit block can be parsed."""
    if "<0x" not in text:
        return text

    def decode(m: re.Match) -> str:
        raw = bytes(int(h, 16) for h in re.findall(r"<0x([0-9A-Fa-f]{2})>", m.group(0)))
        return raw.decode("utf-8", errors="replace")

    return _BYTE_TOKENS.sub(decode, text)


def urlopen(url_or_request, timeout: float):
    """urllib's urlopen with certificate verification that works on every Python build."""
    url = url_or_request if isinstance(url_or_request, str) else url_or_request.full_url
    if url.startswith("https:"):
        return urllib.request.urlopen(url_or_request, timeout=timeout, context=ssl_context())
    return urllib.request.urlopen(url_or_request, timeout=timeout)


OLLAMA_WAIT = 300.0  # seconds a busy Ollama server gets before a run counts it unreachable
OLLAMA_DOWN_WAIT = 15.0  # ... and one that refuses connections (not running)


def ollama_get(base: str, path: str, timeout: float = 10.0, wait: float | None = None) -> dict:
    """GET an Ollama API endpoint (``/api/tags``, ``/api/ps``) as JSON, trying again
    with growing pauses: a busy server is not a server that is down. Ollama on
    Windows can stall for more than a minute after unloading a large model, and a
    single 10-second attempt cost whole benchmark runs. Timeouts and server errors
    are retried for ``wait`` seconds (OLLAMA_WAIT), refused connections only for
    OLLAMA_DOWN_WAIT, so a server that is not running is still reported quickly.
    The last error is raised."""
    wait = OLLAMA_WAIT if wait is None else wait
    start = time.monotonic()
    delay = 1.0
    while True:
        try:
            with urllib.request.urlopen(f"{base.rstrip('/')}{path}", timeout=timeout) as resp:
                return json.loads(resp.read())
        except (OSError, ValueError) as e:
            refused = isinstance(getattr(e, "reason", e), ConnectionRefusedError)
            limit = min(wait, OLLAMA_DOWN_WAIT) if refused else wait
            if time.monotonic() - start + delay > limit:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


def _stream_lines(url: str, body: dict, timeout: float, headers: dict | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with urlopen(req, timeout=timeout) as resp:
        for raw in resp:  # leaving this loop early closes the connection: the server stops
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                yield line


@dataclass
class CallStats:
    """Timing of one request as reported by the server (Ollama reports all of it)."""
    model: str
    total_seconds: float = 0.0
    load_seconds: float = 0.0  # loading the model into memory (first call, or after a swap)
    prompt_tokens: int = 0
    prompt_seconds: float = 0.0
    gen_tokens: int = 0
    gen_seconds: float = 0.0
    stopped: bool = False  # runaway answer cut off
    cost_usd: float = 0.0  # as reported by the provider (OpenRouter); 0 for local models
    provider: str = "ollama"


OPENROUTER_URL = "https://openrouter.ai/api/v1"
REMOTE_PROVIDERS = {"openrouter"}


def provider_of(spec, cfg: LLMConfig) -> str:
    return spec.provider or cfg.provider


def base_url_of(spec, cfg: LLMConfig) -> str:
    provider = provider_of(spec, cfg)
    if spec.base_url:
        return spec.base_url.rstrip("/")
    if provider == "openrouter":
        return OPENROUTER_URL
    return cfg.base_url.rstrip("/")


def openrouter_key(cfg: LLMConfig) -> str:
    """The OpenRouter key: from the environment, else from the key file. Never logged."""
    key = os.environ.get(cfg.openrouter_key_env, "").strip()
    if key or not cfg.openrouter_key_file:
        return key
    try:
        with open(os.path.expanduser(cfg.openrouter_key_file)) as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r"sk-or-[A-Za-z0-9_-]+", text)
    return m.group(0) if m else text.strip()


def remote_models(cfg: LLMConfig) -> list[str]:
    """Models whose prompts leave the machine."""
    return [s.name for s in cfg.models if provider_of(s, cfg) in REMOTE_PROVIDERS]


class Ensemble:
    """Picks a model per request by weight; retries transient failures."""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def last_stats(self) -> CallStats | None:
        """Stats of the calling thread's most recent request."""
        return getattr(self._local, "stats", None)

    def pick(self, rng: random.Random):
        specs = self.cfg.models
        return rng.choices(specs, weights=[max(s.weight, 0.0) for s in specs])[0]

    def complete(self, messages: Messages, rng: random.Random,
                 model: str | None = None, max_chars: int = 0) -> tuple[str, str]:
        """``max_chars`` > 0 stops a runaway answer early (streamed; the server is told)."""
        spec = self.pick(rng)
        if model is not None:
            spec = next((s for s in self.cfg.models if s.name == model), None) or \
                type(spec)(model)
        name = spec.name
        provider = provider_of(spec, self.cfg)
        base = base_url_of(spec, self.cfg)
        self._local.stats = CallStats(name, provider=provider)
        t_start = time.monotonic()
        try:
            return self._complete(messages, spec, name, provider, base, max_chars, model, t_start)
        finally:
            # Failed requests cost time too; record it.
            if not self._local.stats.total_seconds:
                self._local.stats.total_seconds = time.monotonic() - t_start

    def _complete(self, messages: Messages, spec, name: str, provider: str, base: str,
                  max_chars: int, model: str | None, t_start: float) -> tuple[str, str]:
        temp = spec.temperature if (spec.temperature is not None and model is None) \
            else self.cfg.temperature
        last: Exception | None = None
        for attempt in range(4):
            try:
                if provider == "ollama":
                    text = self._ollama(messages, name, temp, max_chars, base)
                elif provider in ("openai", "openrouter"):
                    text = self._openai(messages, name, temp, max_chars, base, provider)
                else:
                    raise LLMError(f"unknown provider {provider!r}")
                self._local.stats.total_seconds = time.monotonic() - t_start
                return text, name
            except Runaway as e:
                self._local.stats.stopped = True
                self._local.stats.total_seconds = time.monotonic() - t_start
                raise LLMError(f"{name}: answer stopped: {e}", e.partial) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 402, 403, 404):
                    detail = e.read().decode(errors="replace")[:300]
                    raise LLMError(f"{name}: HTTP {e.code}: {detail}") from None
                if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                    # Rate limited (common for free cloud models): wait as told, or back off.
                    if attempt == 3:
                        raise LLMError(f"{name}: rate limited (HTTP 429); try again later") \
                            from None
                    try:
                        wait = float(e.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        wait = 0.0
                    time.sleep(min(120.0, wait or 10.0 * 2 ** attempt))
                    continue
                if _is_timeout(e):
                    # Retrying would restart a model load that was merely slow, from scratch.
                    raise LLMError(f"{name}: no answer within {self.cfg.timeout:.0f}s "
                                   "(model still loading, or too slow)") from None
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"{name}: request failed after retries: {last}")

    def _guard(self, parts: list[str], max_chars: int) -> None:
        text_len = sum(map(len, parts))
        if max_chars and text_len > max_chars:
            raise Runaway(f"exceeded {max_chars} characters", "".join(parts))
        if len(parts) % 64 == 0 and looks_repetitive("".join(parts)):
            raise Runaway("the answer started repeating itself", "".join(parts))

    def _ollama(self, messages: Messages, model: str, temp: float, max_chars: int = 0,
                base: str | None = None) -> str:
        body = {
            "model": model, "messages": messages, "stream": True,
            "keep_alive": self.cfg.keep_alive,
            # Reasoning tokens count against num_predict; leave room for the answer.
            "options": {"temperature": temp, "num_ctx": self.cfg.num_ctx,
                        "num_predict": self.cfg.max_tokens * (2 if self.cfg.think else 1)},
        }
        if self.cfg.think is not None:
            body["think"] = self.cfg.think
        parts: list[str] = []
        thinking = 0
        for line in _stream_lines(f"{base or self.base}/api/chat", body, self.cfg.timeout):
            r = json.loads(line)
            if r.get("error"):
                raise LLMError(f"{model}: {r['error']}")
            msg = r.get("message") or {}
            if msg.get("content"):
                parts.append(msg["content"])
                self._guard(parts, max_chars)
            if msg.get("thinking"):
                thinking += len(msg["thinking"])
                if max_chars and thinking > 4 * max_chars:
                    raise Runaway(f"reasoning exceeded {4 * max_chars} characters")
            if r.get("done"):
                with self._lock:
                    self.tokens_in += r.get("prompt_eval_count", 0) or 0
                    self.tokens_out += r.get("eval_count", 0) or 0
                st = self._local.stats
                st.load_seconds = (r.get("load_duration") or 0) / 1e9
                st.prompt_tokens = r.get("prompt_eval_count") or 0
                st.prompt_seconds = (r.get("prompt_eval_duration") or 0) / 1e9
                st.gen_tokens = r.get("eval_count") or 0
                st.gen_seconds = (r.get("eval_duration") or 0) / 1e9
                break
        return decode_byte_tokens("".join(parts))

    def _openai(self, messages: Messages, model: str, temp: float, max_chars: int = 0,
                base: str | None = None, provider: str = "openai") -> str:
        """OpenAI-compatible chat completions, streamed (also OpenRouter)."""
        if provider == "openrouter":
            key = openrouter_key(self.cfg)
            if not key:
                raise LLMError(f"{model}: no OpenRouter key: set ${self.cfg.openrouter_key_env} "
                               f"or put it in {self.cfg.openrouter_key_file}")
        else:
            key = os.environ.get(self.cfg.api_key_env, "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        base = (base or self.base).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        body = {"model": model, "messages": messages, "temperature": temp,
                "max_tokens": self.cfg.max_tokens * (2 if self.cfg.think else 1), "stream": True,
                "stream_options": {"include_usage": True}}
        if provider == "openrouter":
            headers["X-Title"] = "OMEGA TRANSFORM"
            body["usage"] = {"include": True}  # report the cost of the request
            if self.cfg.think is not None:
                body["reasoning"] = {"enabled": bool(self.cfg.think)}
        st = self._local.stats
        parts: list[str] = []
        reasoning = 0
        t0 = time.monotonic()
        first = last = None
        for line in _stream_lines(f"{base}/chat/completions", body, self.cfg.timeout, headers):
            if not line.startswith("data:"):
                continue  # SSE comments, e.g. OpenRouter's keep-alive ": OPENROUTER PROCESSING"
            data = line[5:].strip()
            if data == "[DONE]":
                break
            r = json.loads(data)
            if r.get("error"):
                raise LLMError(f"{model}: {r['error'].get('message', r['error'])}")
            for ch in r.get("choices") or []:
                delta = ch.get("delta") or {}
                think = delta.get("reasoning") or delta.get("reasoning_content")
                piece = delta.get("content")
                if think or piece:
                    now = time.monotonic()
                    first = first if first is not None else now
                    last = now
                if think:
                    reasoning += len(think)
                    if max_chars and reasoning > 4 * max_chars:
                        raise Runaway(f"reasoning exceeded {4 * max_chars} characters",
                                      "".join(parts))
                if piece:
                    parts.append(piece)
                    self._guard(parts, max_chars)
            usage = r.get("usage") or {}
            if usage:
                st.prompt_tokens = usage.get("prompt_tokens", 0) or 0
                st.gen_tokens = usage.get("completion_tokens", 0) or 0
                st.cost_usd = float(usage.get("cost") or 0.0)
                with self._lock:
                    self.tokens_in += st.prompt_tokens
                    self.tokens_out += st.gen_tokens
                    self.cost_usd += st.cost_usd
        if first is not None:
            st.prompt_seconds = first - t0  # time to first token: queueing plus prompt
            st.gen_seconds = (last or first) - first
        return "".join(parts)

    def check(self, reserve_gb: float = 6.0,
              measured: dict[str, float] | None = None) -> list[str]:
        """Return problems with the configured models (empty list = fine): missing, or
        too large for the memory free right now."""
        specs = list(self.cfg.models)
        if self.cfg.insights_model and all(s.name != self.cfg.insights_model for s in specs):
            specs.append(type(specs[0])(self.cfg.insights_model))
        problems: list[str] = []
        local = {s.name for s in specs if provider_of(s, self.cfg) == "ollama"}
        remote = [s for s in specs if provider_of(s, self.cfg) == "openrouter"]
        if local:
            try:
                have = {m["name"] for m in ollama_get(self.base, "/api/tags").get("models", [])}
            except Exception as e:  # noqa: BLE001 - report any connection problem
                return [f"cannot reach ollama at {self.base}: {e}"]
            problems += [f"model {n!r} is not installed (ollama pull {n})"
                         for n in sorted(local) if n not in have and f"{n}:latest" not in have]
            problems += self._memory_problems(local, reserve_gb, measured or {})
        if remote:
            problems += self._openrouter_problems(remote)
        return problems

    def _openrouter_problems(self, specs) -> list[str]:
        """A key must be set, and each model must exist (the model list is public)."""
        out = []
        if not openrouter_key(self.cfg):
            out.append(f"OpenRouter models need a key: set ${self.cfg.openrouter_key_env} or "
                       f"put it in {self.cfg.openrouter_key_file}")
        try:
            base = base_url_of(specs[0], self.cfg)
            with urlopen(f"{base}/models", timeout=15) as resp:
                ids = {m["id"] for m in json.loads(resp.read()).get("data", [])}
        except (OSError, ValueError, KeyError) as e:
            return out + [f"cannot reach OpenRouter: {e}"]
        out += [f"OpenRouter has no model {s.name!r}" for s in specs if s.name not in ids]
        return out

    def _memory_problems(self, names: set[str], reserve_gb: float,
                         measured: dict[str, float]) -> list[str]:
        """Models that would not fit in the memory free right now (not loaded yet)."""
        from .memory import fits
        try:
            sizes = {m["name"]: m.get("size", 0)
                     for m in ollama_get(self.base, "/api/tags")["models"]}
            loaded = {m["name"]: m.get("size", 0) / 1e9
                      for m in ollama_get(self.base, "/api/ps").get("models", [])}
        except (OSError, ValueError, KeyError):
            return []
        out = []
        for n in sorted(names):
            n = n if n in sizes else f"{n}:latest"
            if n in loaded or n not in sizes:
                continue  # already in memory, or unknown
            # A measured loaded size (omega models) beats the conservative estimate.
            ok, why = fits(sizes[n], reserve_gb, measured_gb=measured.get(n))
            if not ok:
                out.append(f"model {n!r} would not fit in memory: {why} "
                           f"(close applications, or lower --reserve-gb)")
        return out


class ScriptedLLM:
    """Test double: answers with ``fn(messages)``."""

    def __init__(self, fn: Callable[[Messages], str], name: str = "scripted"):
        self.fn, self.name = fn, name
        self.tokens_in = self.tokens_out = 0

    def complete(self, messages: Messages, rng: random.Random,
                 model: str | None = None, max_chars: int = 0) -> tuple[str, str]:
        return self.fn(messages), self.name

    def check(self, reserve_gb: float = 0.0, measured=None) -> list[str]:
        return []

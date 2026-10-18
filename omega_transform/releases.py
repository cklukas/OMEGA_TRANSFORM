# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""When each benchmarked model was released, for a timeline per developer.

Ollama does not record when a model was released (its pages only say when a tag
was last updated), so the dates come from the model's original publication:

- local models: the creation date of the original Hugging Face repository,
  named per model in ``bench/model-releases.json`` (an Ollama name does not tell
  which repository it came from, so the mapping is kept by hand);
- OpenRouter models: the ``created`` timestamp of OpenRouter's model list.

``omega model-releases`` fills in the dates; the file is committed, so the
benchmark page is rendered without network access.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .llm import OPENROUTER_URL, urlopen

HF_API = "https://huggingface.co/api/models"
DEFAULT_FILE = Path("bench/model-releases.json")


def load(path: Path = DEFAULT_FILE) -> dict[str, dict]:
    """{model: {"repo": ..., "released": "YYYY-MM-DD", "source": ...}}"""
    try:
        return json.loads(Path(path).read_text()).get("models", {})
    except (OSError, ValueError):
        return {}


def save(models: dict[str, dict], path: Path = DEFAULT_FILE) -> None:
    Path(path).write_text(json.dumps(
        {"note": "release dates of the benchmarked models; refresh with `omega model-releases`",
         "models": dict(sorted(models.items()))}, indent=1) + "\n")


def released(models: dict[str, dict], model: str) -> str | None:
    return (models.get(model) or {}).get("released")


def params_b(text: str | None) -> float | None:
    """Billions of parameters from Ollama's "25.2B" or a name like "nemotron-3-super-120b"."""
    if not text:
        return None
    m = re.fullmatch(r"\s*([\d.]+)\s*([BM])\s*", text, re.I)
    if m:
        return float(m.group(1)) / (1000 if m.group(2).upper() == "M" else 1)
    m = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)b(?![a-z])", text.lower())
    return float(m.group(1)) if m else None


def ollama_params(base_url: str, model: str) -> float | None:
    """Total parameters of an installed Ollama model, in billions."""
    import urllib.request
    req = urllib.request.Request(f"{base_url}/api/show", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return params_b((json.loads(r.read()).get("details") or {}).get("parameter_size"))


def _get_json(url: str):
    with urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def hf_created(repo: str) -> str | None:
    """The creation date of a Hugging Face model repository."""
    created = _get_json(f"{HF_API}/{repo}").get("createdAt")
    return created[:10] if created else None


def openrouter_created() -> dict[str, str]:
    """{model id: release date} for every model OpenRouter lists."""
    return {m["id"]: dt.datetime.fromtimestamp(m["created"], dt.timezone.utc).date().isoformat()
            for m in _get_json(f"{OPENROUTER_URL}/models")["data"] if m.get("created")}


def refresh(models: dict[str, dict], cloud_models: list[str], log=print,
            ollama_url: str | None = "http://localhost:11434") -> dict[str, dict]:
    """Fill in release dates: from Hugging Face for entries with a ``repo``, from
    OpenRouter for the given cloud models. Entries that cannot be resolved keep
    their earlier date. Parameter counts (``params_b``) come from Ollama for local
    models and from the name for cloud models."""
    out = {k: dict(v) for k, v in models.items()}
    for name, entry in out.items():
        if entry.get("repo") and ollama_url:
            try:
                size = ollama_params(ollama_url, name)
            except (OSError, ValueError):
                size = None
            if size:
                entry["params_b"] = size
    for name, entry in out.items():
        if not entry.get("repo"):
            continue
        try:
            date = hf_created(entry["repo"])
        except (OSError, ValueError) as e:
            log(f"  {name}: {entry['repo']} not resolved ({e})")
            continue
        if date:
            entry.update(released=date, source="huggingface")
    if cloud_models:
        try:
            created = openrouter_created()
        except (OSError, ValueError) as e:
            log(f"  OpenRouter model list not available ({e})")
            created = {}
        for name in cloud_models:
            if name in created:
                out.setdefault(name, {}).update(released=created[name], source="openrouter")
                size = params_b(name)
                if size and "params_b" not in out[name]:
                    out[name]["params_b"] = size
            elif name not in out:
                log(f"  {name}: not listed by OpenRouter")
    return out

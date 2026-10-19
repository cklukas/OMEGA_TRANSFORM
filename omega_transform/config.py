# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Problem configuration (``omega.toml``) with defaults for everything optional."""
from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class ModelSpec:
    name: str  # model name, e.g. "qwen3.5:latest" (Ollama) or "qwen/qwen3-coder" (OpenRouter)
    weight: float = 1.0  # relative probability of picking this model per request
    temperature: float | None = None  # None: use llm.temperature
    provider: str | None = None  # "ollama", "openai" or "openrouter"; None: llm.provider
    base_url: str | None = None  # server of this model; None: the provider's default


@dataclass
class LLMConfig:
    #: "ollama" (local), "openai" (any OpenAI-compatible server at base_url) or
    #: "openrouter" (cloud: prompts, including your code, leave the machine)
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"  # Ollama or OpenAI-compatible server address
    #: models to use: names, or tables with name, weight and temperature
    models: list[ModelSpec] = field(default_factory=lambda: [ModelSpec("qwen3.5:latest")])
    temperature: float = 0.8  # sampling temperature (per-model override possible)
    num_ctx: int = 32768  # context window in tokens; also sizes the prompt budget
    max_tokens: int = 8192  # answer token limit (doubled when thinking is on)
    max_answer_chars: int = 0  # stop longer answers early; 0 = auto (2 x program + 8000)
    timeout: float = 900.0  # seconds without data before a request fails
    concurrency: int = 1  # parallel requests; match OLLAMA_NUM_PARALLEL
    think: bool | None = None  # ollama "think" flag for reasoning models; None = model default
    max_consecutive_errors: int = 5  # stop the run when this many requests fail in a row; 0 = never
    keep_alive: str = "30m"  # how long Ollama keeps the model loaded between requests
    insights_model: str | None = None  # model for the lessons notebook; None = first model
    api_key_env: str = "OPENAI_API_KEY"  # environment variable holding the openai provider's key
    openrouter_key_env: str = "OPENROUTER_API_KEY"  # environment variable holding the OpenRouter key
    #: file holding the OpenRouter key, read when the environment variable is not set;
    #: keep it readable only by you (chmod 600)
    openrouter_key_file: str = "~/openrouter.txt"
    max_cost_usd: float = 0.0  # stop the run once paid requests cost this much; 0 = no limit


@dataclass
class Stage:
    name: str = "main"  # stage name, passed as {stage}
    args: list[str] = field(default_factory=list)  # extra arguments appended to the command
    timeout: float = 120.0  # seconds before the stage is killed
    min_score: float | None = None  # cascade gate: below this, later stages are skipped
    exclusive: bool = False  # run alone (no other evaluation in parallel), e.g. timing runs


@dataclass
class EvaluatorConfig:
    #: evaluator command, run in the problem directory once per stage
    command: list[str] = field(default_factory=lambda: ["{python}", "evaluate.py", "{candidate}"])
    stages: list[Stage] = field(default_factory=lambda: [Stage()])  # cascade, cheapest first
    build: list[str] = field(default_factory=list)  # e.g. ["clang++", "-O3", "{candidate}", "-o", "{binary}"]
    build_timeout: float = 120.0  # seconds before the build is killed
    #: separate compilation (instead of build): compile only the candidate to {object} ...
    compile: list[str] = field(default_factory=list)
    #: ... then link {object} with the fixed harness sources into {binary}
    link: list[str] = field(default_factory=list)
    #: global symbols the candidate's object may define (its interface, e.g. ["sort_u32"]);
    #: anything else global is rejected before linking, so it cannot replace functions
    #: the harness or the C library provide. Requires compile and link.
    exports: list[str] = field(default_factory=list)
    launcher: list[str] = field(default_factory=list)  # prefix for every job, e.g. ["srun", "-n1"]
    concurrency: int = 4  # evaluation jobs in parallel
    #: confine jobs: no network, writes only in the job dir (macOS sandbox-exec, Linux
    #: bubblewrap); None = on where available
    sandbox: bool | None = None
    #: run the candidate (Python module, or compiled program) in its own process that cannot
    #: read the problem directory; the evaluator reaches it through omega_transform.harness.
    #: None = on for Python, C and C++
    isolate: bool | None = None
    max_memory_mb: float = 4096.0  # kill a job whose process tree uses more; 0 = no limit
    confirm_runs: int = 0  # extra evaluations of an apparent new best; its score = median
    keep_jobs: bool = False  # keep job directories for debugging
    python: str = sys.executable  # interpreter for {python}; default: the one running the engine


@dataclass
class Feature:
    """One MAP-Elites axis. ``name`` is a key of the evaluator's ``features``
    output, or the builtin ``code_size`` (editable chars relative to the
    initial program)."""
    name: str  # evaluator feature name, or the builtin code_size
    min: float  # lower end of the axis (smaller values go to the first bin)
    max: float  # upper end of the axis (larger values go to the last bin)
    bins: int = 6  # number of bins
    log: bool = False  # bin on a logarithmic scale


@dataclass
class SearchConfig:
    iterations: int = 200  # model answers per run (fix-up retries not counted)
    max_minutes: float = 0.0  # 0 = unlimited
    target_score: float | None = None  # stop when a program reaches this score
    islands: int = 4  # independent sub-populations; iterations rotate over them
    migration_interval: int = 40  # completed evaluations between ring migrations
    exploit_ratio: float = 0.6  # parent = strong program of the island
    explore_ratio: float = 0.25  # parent = random niche elite (diversity)
    repair_ratio: float = 0.1  # parent = a recent failed attempt, asked to fix it
    # remainder: parent = global best
    fix_retries: int = 1  # immediate LLM fix-up attempts after a crash or compile error
    rewrite_ratio: float = 0.15  # ask for a full rewrite of the block instead of a diff
    bold_ratio: float = 0.3  # ask for a substantially different approach
    tune_ratio: float = 0.15  # ask to expose constants and SWEEP them (if sweeps are on)
    adaptive: bool = True  # learn style/edit weights from results (ratios above are priors)
    num_inspirations: int = 2  # other strong programs shown in each prompt
    journal_lines: int = 12  # recent attempts shown per prompt
    insights_interval: int = 20  # completed evaluations between lessons-notebook updates; 0 = off
    sweep_budget: int = 16  # max engine-run variants per LLM-proposed SWEEP; 0 = sweeps off
    seed: int = 0  # random seed of the search decisions
    #: MAP-Elites axes; a program competes only with programs in its own cell
    features: list[Feature] = field(
        default_factory=lambda: [Feature("code_size", 0.25, 4.0, bins=6, log=True)]
    )


@dataclass
class ProblemConfig:
    name: str  # problem name, used in reports
    language: str  # "python" | "cpp" | "c"
    initial: str  # initial program, relative to the problem directory
    description: str = ""  # the task, shown in every prompt
    metrics_help: str = ""  # explains the evaluator's metrics to the LLM
    hints: str = ""  # human expert hints, literature pointers
    candidate_name: str = ""  # filename inside the job dir; default = basename of initial
    category: str = ""  # groups the example in the documentation's example gallery
    summary: str = ""  # one sentence on the problem, for the documentation's start page
    #: fixed files shown read-only in every prompt (e.g. a separately compiled harness)
    context_files: list[str] = field(default_factory=list)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    problem_dir: Path = Path(".")

    @property
    def initial_path(self) -> Path:
        return self.problem_dir / self.initial

    @property
    def lang_tag(self) -> str:
        return {"python": "python", "cpp": "cpp", "c++": "cpp", "c": "c"}.get(self.language, self.language)


def _build(cls, data: Any):
    """Construct nested dataclasses from plain TOML data, rejecting unknown keys."""
    if not is_dataclass(cls):
        return data
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown key(s) in [{cls.__name__}]: {', '.join(sorted(unknown))}")
    lists = {"models": ModelSpec, "stages": Stage, "features": Feature}
    sections = {"evaluator": EvaluatorConfig, "llm": LLMConfig, "search": SearchConfig}
    kwargs = {}
    for name, value in data.items():
        if name in lists:
            kwargs[name] = [_build(lists[name], {"name": v} if isinstance(v, str) else v)
                            for v in value]
        elif name in sections:
            kwargs[name] = _build(sections[name], value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_config(path: str | os.PathLike) -> ProblemConfig:
    p = Path(path)
    if p.is_dir():
        p = p / "omega.toml"
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    problem = dict(raw.get("problem", {}))
    for section in ("evaluator", "llm", "search"):
        if section in raw:
            problem[section] = raw[section]
    extra = set(raw) - {"problem", "evaluator", "llm", "search"}
    if extra:
        raise ValueError(f"unknown section(s) in {p}: {', '.join(sorted(extra))}")
    cfg = _build(ProblemConfig, problem)
    cfg.problem_dir = p.parent.resolve()
    if not cfg.candidate_name:
        cfg.candidate_name = Path(cfg.initial).name
    if bool(cfg.evaluator.compile) != bool(cfg.evaluator.link):
        raise ValueError("evaluator.compile and evaluator.link go together")
    if cfg.evaluator.exports and not cfg.evaluator.compile:
        raise ValueError("evaluator.exports needs separate compilation (compile and link)")
    if cfg.language in ("cpp", "c++", "c") and not cfg.evaluator.build \
            and not cfg.evaluator.compile:
        cxx = shutil.which("clang++") or shutil.which("g++") or "c++"
        cfg.evaluator.build = [cxx, "-O2", "-std=c++20", "-pthread", "{candidate}", "-o", "{binary}"]
    if not cfg.initial_path.exists():
        raise FileNotFoundError(f"initial program not found: {cfg.initial_path}")
    return cfg

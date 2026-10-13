<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Getting started

## Requirements

- macOS or Linux with Python 3.11 or newer. The engine uses only the standard
  library.
- [Ollama](https://ollama.com) running locally, with at least one model
  pulled. `qwen3.5:latest` (9.7B, about 7 GB) is the default.
- For C/C++ problems: `clang++` or `g++`.

Jobs run sandboxed automatically: `sandbox-exec` on macOS, bubblewrap (`bwrap`)
on Linux (see [Robustness and safety](robustness.md)).

## Install

Use the repository directly:

```bash
python3 -m omega_transform --help
```

Or install the `omega` command:

```bash
python3 -m pip install -e .
```

The examples below use `python3 -m omega_transform`; `omega` is the same.

## Check a problem

`check` evaluates the initial program once and verifies that the configured
model is installed:

```bash
python3 -m omega_transform check examples/circle_packing
```

```text
problem   circle_packing_26 (python)  .../examples/circle_packing/initial.py
evaluator {python} evaluate.py {candidate}  stages: main
models    qwen3.5:latest via ollama http://localhost:11434
initial   ok  score 1.4901  (0.08s, stage main)
```

## Run a search

```bash
python3 -m omega_transform run examples/circle_packing -n 50
```

Each finished iteration prints one line. `★` marks a new best, `✗` a failed
attempt, with the reason:

```text
✗ it    4 i0 #69af98 g1 FAILED: invalid result [exploit] IDEA: ring arrangement … | expected 26 circles, got 38
★ it    3 i3 #48d951 g1 score 1.87941 (+0.3893) [exploit] IDEA: iteratively expand radii …
  sweep #69fdbc: 9 variants in 14s
```

`--minutes 30` sets a time limit instead of an iteration count. Ctrl-C stops
the run after the iterations in flight; running the same command again with
`--run-dir` resumes it.

## Look at the results

The run directory (`examples/circle_packing/runs/<timestamp>/`) holds the
database, the best program (`best_initial.py`), every prompt and answer
(`llm/`), and the lessons notebook. To inspect them:

```bash
python3 -m omega_transform status examples/circle_packing/runs/<timestamp>
```

```bash
python3 -m omega_transform lineage examples/circle_packing/runs/<timestamp>
```

```bash
python3 -m omega_transform stats examples/circle_packing/runs/<timestamp>
```

`lineage` prints the chain of ideas that led to the best program; `stats`
shows which strategies paid off. All commands are listed in
[Command line](cli.md).

## The examples

The [example gallery](examples.md) describes all of them; the first three were
the original ones:

| Example | Language | Score |
|---|---|---|
| `examples/circle_packing` | Python | Sum of the radii of 26 non-overlapping circles in the unit square; the best known value is about 2.635 |
| `examples/matmul_cpp` | C++ | GFLOPS of a multithreaded float matrix multiply at n = 1024, correctness-gated, wall-clock time, timed alone |
| `examples/matmul_st_cpp` | C++ | GFLOPS per CPU-second of a single-threaded matrix multiply at n = 768; robust to other load, used by the model benchmark |
| `examples/toy` | Python | Used by the test suite |

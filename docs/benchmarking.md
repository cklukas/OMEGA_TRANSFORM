<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Benchmarking models

The examples double as a model benchmark: `omega bench` runs the same
time-boxed search for every installed Ollama model on every example, and
records one result file per run. The [Model benchmarks](benchmarks.md) page is
generated from those files.

## Checking which models work

Installed models are not always usable: a download may be broken, a model may
not fit in memory next to the evaluator, or it may run too slowly to be worth
hours of benchmark time. `omega models` finds out in a few minutes per model:

```bash
python3 -m omega_transform models
```

For each model it measures the load time, the generation speed and the
memory, and checks three things: whether the model answers a greeting
sensibly, whether it can produce a three-line edit in the engine's
SEARCH/REPLACE format that applies, and whether its reasoning mode works. The
model is unloaded afterwards. The verdict is `ok`, `slow` (below 8 generated
tokens per second), `broken` (no sensible answer), `failed to load`,
`not enough RAM` or `too large`. The last two are decided before loading: a
model is never loaded when its estimated size plus a reserve (`--reserve-gb`,
default 6 GB) exceeds the memory free at that moment, other applications
included, or when its weights exceed `--max-size-gb`. The benchmark repeats
the memory check before every run and unloads each model when its run ends. The result goes to
`bench/model-check.json`; `bench --feasible bench/model-check.json` then runs
only the models marked ok, and the benchmark page shows the check as its
first table.

## Running the full benchmark

`scripts/run-benchmarks.sh` is the repeatable way to benchmark: it checks
the installed models first, then runs every model marked ok on every example
twice, without and with the model's reasoning mode ("thinking"; models without one are skipped in that pass),
regenerates this site's benchmark page, and writes a log to `logs/`. Rerun it
after changing the engine, the prompts or the examples, or after pulling new
models:

```bash
scripts/run-benchmarks.sh
```

With the default 15 minutes per run this takes several hours; on macOS the
script keeps the machine awake. `MINUTES` and `MODES` change the budget and
the reasoning modes, and any `omega bench` option can be appended:

```bash
MINUTES=5 MODES=think scripts/run-benchmarks.sh --models qwen3.5:latest --dry-run
```

On a shared machine, the script can wait for room instead of skipping every
model that does not fit: `WAIT_LOAD` waits until the 1-minute load average is
below a value, `WAIT_RAM_GB` until at least that much memory is free (measured
like the memory guard measures it), and both must hold at the same time.
`WAIT_HOURS` (default 12) bounds the wait; when it expires, nothing runs and the
log says why. Queued like this, a benchmark starts by itself, for example once
other work has ended overnight:

```bash
WAIT_LOAD=32 WAIT_RAM_GB=14 RESERVE_GB=4 WAIT_HOURS=24 scripts/run-benchmarks.sh
```

## Running a benchmark by hand

List what would run, and the expected duration:

```bash
python3 -m omega_transform bench --dry-run
```

Run all models on both examples, 15 minutes each:

```bash
python3 -m omega_transform bench
```

Useful options:

| Option | Effect |
|---|---|
| `--models A B` | Only these models (default: all installed) |
| `--exclude A B` | Leave these out |
| `--max-size-gb 22` | Skip models whose weights exceed this size; they would not fit next to the evaluator in memory |
| `--minutes 15` | Search budget per run; the first call includes loading the model |
| `--examples DIR …` | Problem directories (default: circle packing and the single-threaded matmul, whose CPU-time score stays comparable on a busy machine) |
| `--think` | Enable the reasoning mode of models that support it |

Tags that point to the same weights (same digest) run only once. A model that
fails to load is recorded as failed and the benchmark continues.

## What is recorded

Each run writes `bench/results/<time>_<example>_<model>.json` with:

- the initial and best score, and their ratio;
- the number of model calls and their duration (mean, median, 90th percentile);
- the model's inference speed: load time, prompt and generation tokens per
  second, and tokens per answer, as reported by Ollama for every call;
- the model's memory while loaded, as reported by Ollama (weights, context
  cache and buffers; shared memory on Apple silicon);
- the outcome of every answer (valid, edit not applicable, compile error,
  crash, timeout, runaway answer stopped);
- the best score over time, the number of new bests, and the best idea;
- how the best program came about: its ancestry from the initial program on
  (minute, score, outcome, kind of change and idea per step), and its diff
  to the initial program;
- the settings, the OMEGA TRANSFORM version and commit, and the machine.

The full run directories go to `bench/runs/`, which is not committed. They
can be inspected with the usual commands (`status`, `lineage`, `stats`).

## Publishing results

After each run, the benchmark regenerates `docs/benchmarks.md`. To publish,
commit the new files in `bench/results/`. The CI build regenerates the page
from the committed results before it builds the site, so the published
tables and charts always match the committed numbers. To regenerate the page
by hand:

```bash
python3 -m omega_transform report
```

Each example's own page (`docs/examples/<name>.md`, linked from the
[example gallery](examples.md)) shows the best result across all models, the
path that led there, and the best program as a diff. These pages are
generated with the reference pages and checked by the tests, so regenerate
them after committing new results:

```bash
python3 -m omega_transform docs
```

Result files written before the ancestry and the diff were recorded can get
them from their run directories, as long as `bench/runs/` still holds them:

```bash
python3 -m omega_transform bench-details
```

## Comparing settings without a full run

`omega replay` re-sends prompts that a run already logged, with a different
model or setting, and reports whether the answers would apply and, with
`--evaluate`, how they score. It answers questions like "does reasoning mode
help this model?" in minutes:

```bash
python3 -m omega_transform replay examples/matmul_cpp/runs/<run> -n 5 --think --evaluate
```

## Interpreting results

Run benchmarks on an otherwise idle machine, and with nothing else using the
Ollama server. Speeds that Ollama measures itself (tokens per second, load
time) stay correct under load, but the wall-clock time per call includes
waiting in the server's queue, and the C++ example's timings suffer from any
other work.

One run is one sample of a random search. Differences of a few percent
between models mean little; a model that never produces a valid answer, or
whose answers run away, is a clear signal. The C++ example measures speed,
so its scores also depend on what else the machine was doing. Compare runs
from the same machine only.

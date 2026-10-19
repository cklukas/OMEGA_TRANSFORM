<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Benchmarking models

The examples double as a model benchmark: `omega bench` runs the same
time-boxed search for every installed Ollama model on every example, and
records one result file per run. The [Model benchmarks](benchmarks.md) page
and its sub-pages, such as the comparisons
[by developer](benchmarks/developers.md) and [by country](benchmarks/countries.md),
are generated from those files.

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

## Models that need fixes

Some uploads, mostly community conversions, fail the model check because of
how they are packaged, not because of the model. [Model notes](model-notes.md)
records what was wrong and, where possible, how a model was made usable step
by step (for example LLM-jp-4 8B, whose upload needed a custom Modelfile).

## The overall ranking

Scores of different examples are not comparable: a matrix multiply can gain
×80 while a route heuristic gains ×1.1, so adding up improvement factors would
only rank the C++ kernels. The benchmark page therefore weights every example
equally. Per example, each model's gain over the starting program is divided
by the largest gain any model reached there, giving its share (100% for the
best model, 0% for no gain or a failed run); the ranking is the mean share
over the examples. The geometric mean of the improvement factors, the number
of examples won and the number without any gain are shown alongside, local
and cloud models in separate tables. Models with results on fewer than three
quarters of the examples are listed without a rank.

Below each ranking, the models are grouped by their developer (Google for
Gemma, OpenAI for gpt-oss, Alibaba for Qwen, and so on): the developer's best
single model, the mean share over all its models, and the mean share when its
best model is picked for each example separately. The last column favours
developers with many models; the number of models is shown next to it.

A country leaderboard follows: each country is represented by its best
ranked model (the country of a model is that of its developer's
headquarters), and every other country's lag behind the leader is given as
the relative shortfall of its share, 1 − share / leader's share.

## Progress over time

Under each ranking, the page lists every developer's models by release date,
to show whether a developer's newer models do better. Release dates come from
the original publication: the creation date of the model's Hugging Face
repository for local models (`bench/model-releases.json` names the repository
of each Ollama model, since an Ollama name does not tell), and OpenRouter's
listing date for cloud models. Models are compared only within a size class
(under 15B and 15B and up, total parameters), so that a newer but smaller
model does not read as a step back. Add a newly benchmarked local model's
repository to the file, then fill in the dates and sizes:

```bash
python3 -m omega_transform model-releases
```

## Publishing results

After each run, the benchmark regenerates `docs/benchmarks.md`, the overview
with the overall ranking, and its sub-pages in `docs/benchmarks/`: the
comparisons by developer and by country, the model check, and one page per
example. To publish, commit the new files in `bench/results/`. The CI build
regenerates the pages from the committed results before it builds the site,
so the published tables and charts always match the committed numbers. To
regenerate the pages by hand:

```bash
python3 -m omega_transform report
```

Each example's own page (`docs/examples/<name>.md`, linked from the
[example gallery](examples.md)) shows the best result across all models, the
path that led there, and the best program as a diff. Where the evaluator
reports a drawable [solution](defining-problems.md#drawable-solutions), the page
also shows what the starting program and the best program produce, side by
side (`docs/assets/examples/<name>.svg`). These pages are
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

Likewise, result files written before the solutions were recorded (schema 3)
get them by evaluating the initial and the best program of each example's best
result again, taken from its run database:

```bash
python3 -m omega_transform bench-solutions
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

<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Benchmarking models

The examples double as a model benchmark: `omega bench` runs the same
budgeted search for every installed Ollama model (and, optionally, OpenRouter
models) on every example, and records one result file per run. The [Model benchmarks](benchmarks.md) page
and its sub-pages, such as the comparisons
[by developer](benchmarks/developers.md) and [by country](benchmarks/countries.md),
are generated from those files.

## The budget of a run

Every run, local or cloud, with or without reasoning, gets the same budget:

| Limit | Value | Option |
|---|---|---|
| Time | at most 60 minutes of search | `--minutes` |
| Early stop | after 15 model answers in a row without progress | `--patience` |
| Progress | a new best that beats the previous best by at least 0.1% | `search.min_gain` |
| Tokens per answer | 24,576, reasoning included, in both modes | `llm.max_tokens` |
| Reasoning | at most 8 times the answer's character limit | `llm.max_reasoning_factor` |
| Context window | 40,960 tokens; the prompt gets what the answer leaves | `llm.num_ctx` |
| Cost (OpenRouter) | at most $1 of requests per run | `--max-cost-usd` |

A model that keeps improving gets the full hour; a model that is stuck stops
early, so the benchmark spends its time where it changes a result. Small
gains do not count as progress: the timing examples measure run time, whose
noise alone would otherwise produce tiny "new bests" that keep a stuck search
going. The answer's character limit is twice the program plus 8,000
characters; a reasoning model may think for eight times that before its answer
counts as a runaway and is stopped.

Each result records why its run ended (`stop_reason`): `time limit`,
`no progress`, `cost limit`, `model errors` (five failed requests in a row),
`interrupted`, or `error` when the run failed as a whole. The example pages
show it next to the minutes the run took.

The result files carry a version (`schema`, now 4). The report reads only the
current version: results made under an earlier budget are not comparable and
are not shown.

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
included, or when its weights exceed `--max-size-gb`. With discrete NVIDIA
GPUs, the part of the model that fits into their free memory does not count
against the RAM. The benchmark repeats
the memory check before every run and unloads each model when its run ends.
`--skip-existing` resumes an interrupted benchmark: it leaves out every
example and model that already has a result (not an error) in `--out`. The result goes to
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

With up to an hour per run this takes days for many models; on macOS the
script keeps the machine awake. `MINUTES`, `PATIENCE` and `MODES` change the
budget and the reasoning modes, and any `omega bench` option can be appended:

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

List what would run, and the longest it could take:

```bash
python3 -m omega_transform bench --dry-run
```

Run all models on both examples, at most an hour each:

```bash
python3 -m omega_transform bench
```

Useful options:

| Option | Effect |
|---|---|
| `--models A B` | Only these models (default: all installed) |
| `--exclude A B` | Leave these out |
| `--max-size-gb 22` | Skip models whose weights exceed this size; they would not fit next to the evaluator in memory |
| `--minutes 60` | Time limit per run; the first call includes loading the model |
| `--patience 15` | End a run after this many answers in a row without progress; 0 = time limit only |
| `--examples DIR …` | Problem directories (default: circle packing and the single-threaded matmul, whose CPU-time score stays comparable on a busy machine) |
| `--think` | Enable the reasoning mode of models that support it |

Tags that point to the same weights (same digest) run only once. A model that
fails to load is recorded as failed and the benchmark continues.

## What is recorded

Each run writes `bench/results/<time>_<example>_<model>.json` with:

- the initial and best score, and their ratio;
- why the run ended (`stop_reason`) and the minutes it took (`wall_minutes`),
  with the time limit and the patience;
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
- the settings (including the token limits), the OMEGA TRANSFORM version and
  commit, and the machine.

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
equally. Per example and machine, each run's gain over its starting program is
divided by the best **mean** gain any model reached there, giving the run's
share; the reference is a mean so that one lucky run does not set the bar for
everyone (that run may then show more than 100%). A model's share on an
example is the mean over its runs, and its overall share the mean over the
examples: 100% means the best model on every example, 0% no gain. A run whose
model never answered (its result has an `error`, for example "reasoning
exceeded" in think mode) counts as 0%, never as missing; the tables show the
number of runs with the failed ones in brackets. The geometric mean of the
improvement factors, the number of examples won and the number without any
gain are shown alongside, local and cloud models in separate tables. Models
with results on fewer than three quarters of the examples are listed without
a rank.

Where a model was run more than once, its share gets a deviation: the
standard error of the mean share, from the spread between its repeated runs.
On an example with a single run, the model's pooled run-to-run variance from
its repeated examples stands in; a model without any repeated run has no
deviation. Ties follow one simple rule: going down the table in order of
share, a model whose range (share ± standard error) reaches the range of the
first model of the current tie group shares that model's rank, and both are
marked `=`; otherwise it starts a new group, ranked by its position. Comparing
with the group's first model rather than the previous one keeps a chain of
small overlaps from tying the whole table. Without repeated runs the ranges are
single points, so only equal shares tie.

Below each ranking, the models are grouped by their developer (Google for
Gemma, OpenAI for gpt-oss, Alibaba for Qwen, and so on): the developer's best
single model, the mean share over all its models, and the mean share when its
best model is picked for each example separately. The last column favours
developers with many models; the number of models is shown next to it.

A country leaderboard follows: each country is represented by its best
ranked model (the country of a model is that of its developer's
headquarters), and every other country's lag behind the leader is given as
the relative shortfall of its share, 1 − share / leader's share.

## Repeated runs and several machines

All runs are kept: a second or third run of the same example, model and
reasoning mode adds to the statistics instead of replacing the earlier one.
Runs are grouped by example, model, reasoning mode and machine. The machine is
a short label from the `machine` record of each result file: the CPU without
brand words, plus the NVIDIA GPUs, such as "Apple M1 Max" or
"i7-8700K + 2x GTX 1080 Ti". Each example's benchmark page then shows the
number of runs, the mean best score and gain ± their standard deviation (a
failed run counts with its starting program), means of the call statistics,
speed and memory over the completed runs, and error bars of ± 1 standard
deviation on the score, valid-answer, call-time and speed charts (the ranking
chart shows ± the standard error of the share). An error bar is cut off where
the chart's range ends: at 0, and at 100 for percentages. Memory has no error
bars, and a single run shows no deviation.

The error bars use the `error` line of ckdocs' XY charts (ckdocs 0.9.0 and
later), an extension of Mermaid's `xychart-beta`: GitHub's own Markdown
preview cannot render a chart that uses it, the published site can.

Machines are never pooled blindly. Raw scores of the timing examples
(`sort_u32`, `prefix_sum`, `float_parse`, the matrix multiplies) depend on the
CPU and its C library (Windows' `strtod` is about 3.9 times slower than
macOS's), and a faster machine gets more model calls into the same time
(about 1.4 times more on an M1 Max than on an i7-8700K with two GTX 1080 Ti).
So each example page has one section per machine, shares are computed per
machine against that machine's own best mean gain, and with results from more
than one machine the overall ranking comes in two forms: a combined table,
with each machine's share in a column of its own, and one table per machine.
In the combined table a model's shares of one example on several machines
count as repeats: the example's share is their mean (each machine weighted
equally), and the spread between them goes into the standard error, the ±
and the error bars of the ranking chart. That spread holds chance and the
machine's effect, so it is wider than that of repeats on one machine; a model
run on one machine only keeps the deviation of its own repeats, if any.

To merge another machine's results, copy its result files into
`bench/results/` and regenerate the pages; file names carry the start time,
example and model, so they do not collide:

```bash
scp 'workstation:V:/omega_transform/bench-v4/results/*.json' bench/results/
python3 -m omega_transform report
python3 -m omega_transform docs
```

Commit the copied files with the pages, as with any new results.

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
between models mean little unless repeated runs back them; ties in the
ranking mark where they do not. A model that never produces a valid answer,
or whose answers run away, is a clear signal. The C++ examples measure speed,
so their scores also depend on the machine and on what else it was doing:
compare raw scores from the same machine only, and machines through their
shares.

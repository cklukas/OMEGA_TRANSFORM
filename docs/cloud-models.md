<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Cloud models (OpenRouter)

By default OMEGA TRANSFORM uses local models only, and nothing leaves the
machine. Optionally, any model available on [OpenRouter](https://openrouter.ai)
can be used instead of, or next to, the local ones: for example a fast local
model for most requests and a strong cloud model for a few.

**Privacy.** A cloud model receives the full prompts: the problem
description, the hints, the history of attempts, and the code of the program.
Use it only for code you may share with OpenRouter and the model's provider.
Every run that uses a cloud model says so in its first lines. Candidate
programs are still built and run locally, in the sandbox.

## The key

The key is read from the environment variable `OPENROUTER_API_KEY` or, when
that is not set, from the file `~/openrouter.txt` (setting
`llm.openrouter_key_file`). It is never written to a configuration file, a
log or a run database. Keep the key file readable only by you:

```bash
chmod 600 ~/openrouter.txt
```

## Using a cloud model in a problem

Give the model a `provider`. Names are OpenRouter's model ids:

```toml
[llm]
models = [
  { name = "qwen3.5:latest", weight = 0.8 },
  { name = "qwen/qwen3-coder", weight = 0.2, provider = "openrouter" },
]
```

Or use only cloud models with `provider = "openrouter"` in `[llm]`. `check`
verifies that the key is present and that OpenRouter knows each model, before
any paid request:

```bash
python3 -m omega_transform check examples/circle_packing
```

`think` maps to OpenRouter's reasoning switch for models that support one.

## Cost

Every request records what OpenRouter charged for it, next to its token
counts and timing; `omega stats` and the benchmark page report it.
`llm.max_cost_usd` stops a run once its requests have cost that much:

```toml
[llm]
max_cost_usd = 2.0
```

## Which cloud models are worth trying

[OpenRouter models](openrouter-models.md) lists the models the OpenRouter
community uses most, with their prices and a chart of cost against usage.
It is generated from a committed snapshot; refresh it with:

```bash
python3 -m omega_transform openrouter-snapshot
```

```bash
python3 -m omega_transform report
```

Free variants (ids ending in `:free`) cost nothing but are rate-limited and
sometimes overloaded. OMEGA TRANSFORM waits and retries when OpenRouter
answers "too many requests"; `omega models` reports a model that stays
overloaded as `rate limited`.

## Benchmarking cloud models

`omega bench --openrouter MODEL …` adds cloud models to a benchmark. They need
no local memory, so the memory guard does not apply to them, and each run is
capped at `--max-cost-usd` (default 1 dollar):

```bash
python3 -m omega_transform bench --openrouter qwen/qwen3-coder --models qwen3.5:latest
```

`omega models --openrouter MODEL …` checks them first, with three small
requests each.

The timings of a cloud model include the network and the provider's queue:
the generation speed is measured from the first to the last streamed token,
the prompt time is the time to the first token.

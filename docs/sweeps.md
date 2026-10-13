<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Parameter sweeps

LLM calls are the scarce resource, and spending one to nudge a constant from 0.3 to
0.35 wastes it. So the division of labour is: **the LLM decides what to tune and the
engine tunes it.** Any answer may end with

```
SWEEP:
STEP = logrange(1e-4, 1e-1)
ITERS = intrange(100, 2000)
LAYOUT = ["hex", "grid", "ring"]
```

where each name is a constant inside an OMEGA-BLOCK (`NAME = ...` in Python,
`constexpr`/`const`/`#define` in C/C++). The program from the LLM's edit
(the *base*) is evaluated first, as usual. If it succeeds:

1. The engine plans up to `search.sweep_budget` variants (default 16). An
   all-list space that fits the budget is run as a full grid. Otherwise half
   the budget samples at random and the other half refines around the best
   point so far, in a shrinking neighborhood (log scale for `logrange`).
2. Variants run in batches of `evaluator.concurrency` on the job pool, with
   no LLM involved. A benchmark sweep honours `exclusive` stages.
3. Only the **best variant**, and only if it beats the base, enters the
   population, as a child of the base ("tuned X=…, Y=… on top of: …").
   The other variants are stored with status `swept`: kept for deduplication
   and auditing, but hidden from prompts and never parents or elites.
4. The LLM later sees **one line** for the whole sweep, attached to the base
   in the lineage and journal:
   `sweep of 16 points, scores 2.41..2.52 (unswept 2.45) | best: STEP=0.012,
   ITERS=900 -> 2.52; … | mean by LAYOUT: hex:2.50 grid:2.43`.
   This tells it which directions are exhausted and which parameters matter.

A dedicated prompt style, *tune* (`search.tune_ratio`), asks the LLM to find
parameters that are probably suboptimal, expose them as constants, and sweep
them.

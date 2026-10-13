<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Defining a problem

A problem is a directory with three parts: the program to improve, an
evaluator that scores it, and `omega.toml`. The examples in `examples/` are
complete templates.

## The program and its editable regions

The program is one source file. Mark the code the model may change with
marker comments; everything outside the markers is a fixed harness that every
candidate keeps byte for byte:

```python
import math

# OMEGA-BLOCK-START
def construct_packing(n=26):
    ...
# OMEGA-BLOCK-END


def run():
    return construct_packing(26)
```

In C and C++, use `// OMEGA-BLOCK-START` and `// OMEGA-BLOCK-END`. A file may
contain several regions. A file without markers is editable as a whole.

Put everything the evaluator relies on outside the regions: the entry point,
the reference implementation, the timing code, the checks. The model then
cannot change the rules it is judged by. For the same reason, a candidate's
edits are rejected if they touch anything outside the markers.

Write the comments inside the region for the model: say what the function
must do, its constraints, and what the caller guarantees. They are part of
every prompt.

## The evaluator

The evaluator is any command. It runs once per [cascade stage](#cascade-stages)
and must print one JSON object as the last line of its standard output:

```json
{"score": 2.51, "metrics": {"sum_radii": 2.51}, "features": {"spread": 0.1},
 "valid": true, "feedback": "free text shown to the model"}
```

| Key | Meaning |
|---|---|
| `score` | The number to maximize. Required unless `valid` is false. |
| `valid` | `false` marks the candidate invalid; its `feedback` goes to the model. |
| `metrics` | Any numbers or strings; shown to the model with the program. |
| `features` | Numbers usable as [MAP-Elites](how-it-works.md) axes (`search.features`). |
| `feedback` | Text for the model: what is wrong or what limits the score. |

Good feedback is concrete: `circles 3 and 7 overlap by 0.002` helps a model
far more than `invalid`. A crash with no JSON output is recorded as a runtime
error, with the tail of the standard error stream as feedback.

The command line may use these placeholders; the same values are also set as
environment variables:

| Placeholder | Variable | Value |
|---|---|---|
| `{candidate}` | `OMEGA_CANDIDATE` | The candidate source file in its job directory |
| `{binary}` | `OMEGA_BINARY` | The compiled program (C/C++) |
| `{stage}` | `OMEGA_STAGE` | The name of the current stage |
| `{workdir}` | `OMEGA_WORKDIR` | The job directory; the only writable place |
| `{problem_dir}` | `OMEGA_PROBLEM_DIR` | The directory of `omega.toml` |
| `{python}` | | The Python interpreter running the engine |

The evaluator runs in the problem directory, but in the sandbox it may write
only to `$OMEGA_WORKDIR`.

### Calling a Python candidate

A Python evaluator does not import the candidate; it calls the candidate's
functions through `omega_transform.harness`:

```python
from omega_transform.harness import Candidate, CandidateError

cand = Candidate(sys.argv[1])
try:
    tour = cand.call("solve", points, 2.0)
except CandidateError as e:      # import error, exception, missing function
    fail(str(e))                 # concise message plus traceback tail
cpu = cand.last_cpu              # CPU seconds of that call
```

Under the engine, the candidate runs in its own process with a stricter
sandbox: it cannot read the problem directory, so it cannot read the
evaluator, hidden data or reference solutions. Arguments and results cross as
JSON: numbers, strings, lists, dicts and bytes; tuples arrive as lists. Use
`cand.last_cpu` for time limits, because the candidate's work no longer runs
in the evaluator's process. Run by hand, without the engine, the same class
loads the candidate in-process, so `python3 evaluate.py candidate.py` still
works (with the repository on `PYTHONPATH`).

Every build and stage is also measured by the engine, whatever the evaluator
reports: the CPU time and the peak memory of its whole process tree are added
to the program's metrics as `<step>_cpu_s` and `<step>_peak_mb`, for example
`build_cpu_s` and `bench_peak_mb`. The model sees them with the other metrics.

## Wall-clock time or CPU time

A performance score must survive other programs running on the same machine.
Wall-clock time does not: on a busy machine the same program measured 1.37 to
1.50 GFLOPS, and 0.51 while a model was generating. CPU time counts only the
time the program actually ran, and measured 1.63 to 1.66 under load. Two
rules follow:

- For single-threaded work, time it with the process CPU clock
  (`clock_gettime(CLOCK_PROCESS_CPUTIME_ID)` in C/C++,
  `time.process_time()` in Python). Threads cannot cheat this score: their CPU
  time adds up. `examples/matmul_st_cpp` works this way.
- When parallel speed is the point, CPU time is the wrong measure, because
  threads make a program faster but not cheaper. Keep wall-clock time, run
  the stage `exclusive`, and set `confirm_runs`. `examples/matmul_cpp` works
  this way.

## C and C++

Compile the candidate separately from the harness that times and checks it.
In a single file, the editable region's macros would leak into the harness
below it (`#define clock_gettime ...` fakes the timing) and its code could call
the harness's internal functions. Instead, the candidate file holds only the
editable code, the harness is its own file, and the engine builds in two
steps:

```toml
[problem]
initial = "sort.cpp"                 # includes plus the OMEGA-BLOCK region
context_files = ["harness.cpp"]      # shown read-only in every prompt

[evaluator]
compile = ["clang++", "-O3", "-march=native", "-std=c++20", "-c", "{candidate}", "-o", "{object}"]
link = ["clang++", "-O3", "-march=native", "-std=c++20", "{object}", "{problem_dir}/harness.cpp", "-o", "{binary}"]
exports = ["sort_u32"]               # the only global symbol the candidate may define
command = ["{python}", "evaluate.py", "{binary}", "{stage}"]
```

The interface function is declared `extern "C"` in both files. Between the
two steps the engine lists the candidate object's global symbols: anything
unmangled beyond `exports` (which could replace a C library function such as
`clock_gettime` at link time) and replacements of the global `operator new` or
`delete` are refused, with feedback telling the model to make helpers
`static`. The compile step and the compiled program both run without read
access to the problem directory: the evaluator runs the binary through
`Candidate(binary).run([...])` from `omega_transform.harness`.

A failed compile or link is recorded as a compile error; the error lines of
the compiler output, without the long job paths, become the model's feedback.
A single `build` command still works for problems that need no protection.

## Cascade stages

Stages run in order, and a failure ends the evaluation. Put cheap checks first
so broken candidates are rejected fast. A stage with `min_score` also stops
candidates that run but score below the gate; they are recorded as `filtered`.

```toml
stages = [
  { name = "check", timeout = 60 },                     # correctness on small inputs
  { name = "bench", timeout = 120, exclusive = true },  # the timed run
]
```

`exclusive = true` runs the stage with nothing else in parallel: no other
evaluation and no model inference. Use it for anything timed. For noisy
scores, also set `confirm_runs` (see [Robustness and safety](robustness.md)).

## Combining several measurements into one score

When a score combines several inputs, sizes or distributions, add up the work
and the time, and divide: total elements over total CPU seconds, not the
average or geometric mean of per-input rates. A mean of rates lets trivial
inputs dominate: in the sort example, a candidate that only detected
already-sorted input reached 10,000 million elements per second on that input
and tripled a geometric-mean score, while the real sorting work had improved
by half. With total throughput, the slow inputs that need the real work
dominate, as they should.

## Describing the task

The `[problem]` texts go into every prompt:

- `description`: the task, the constraints, and what the program returns.
- `metrics_help`: what the score and each metric mean.
- `hints`: expert knowledge, known techniques, literature. This is the most
  effective lever you have; a small model rarely discovers a technique it has
  not been told about.

## Choosing the search budget

`search.iterations` caps the number of model answers; `search.max_minutes` caps
the time. On one machine with one model, expect one answer every 30 to 120
seconds, depending on model size and answer length. All settings are listed
in [Configuration](configuration.md).

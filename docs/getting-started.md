<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Getting started

## Requirements

- macOS, Linux or Windows 10/11 with Python 3.11 or newer. The engine uses
  only the standard library.
- [Ollama](https://ollama.com) running locally, with at least one model
  pulled. `qwen3.5:latest` (9.7B, about 7 GB) is the default.
- For C/C++ problems: `clang++` or `g++` (on Windows: LLVM's `clang++`, see
  below).

Jobs run sandboxed automatically: `sandbox-exec` on macOS, bubblewrap (`bwrap`)
on Linux, AppContainers on Windows (see [Robustness and safety](robustness.md)).
`omega sandbox` reports which one this machine uses. Without one the engine
refuses to run jobs; only `evaluator.sandbox = false` runs them unconfined.

### Windows

The engine runs natively on Windows (no WSL): Job Objects take the place of
process groups (a timeout or the memory limit kills a job's whole process tree,
and CPU time and peak memory are accounted for the tree), and named pipes
connect an evaluator to its candidate's process. Notes:

- C/C++ examples need a current LLVM (`winget install LLVM.LLVM`) and the
  Visual Studio Build Tools (C++ workload), whose headers and libraries
  LLVM's `clang++` uses. The LLVM installer does not change `PATH`: put
  `C:\Program Files\LLVM\bin` first, ahead of older clang builds that other
  toolchains install. `llvm-nm` from the same directory checks the symbols of
  separately compiled candidates.
- Windows advances a process's CPU times only per scheduler tick (15.6 ms),
  `time.process_time` included. CPU times are therefore taken from the CPU
  cycles a process ran (QueryProcessCycleTime) divided by the timestamp
  counter's rate, which is constant on CPUs with an invariant TSC: precise to
  microseconds, like the POSIX process clock. The C++ harnesses and the
  candidate runner (`cand.last_cpu`) both use it.
- Jobs run in AppContainers, the isolation of Store apps and browser
  sandboxes, started by `omega_transform/winsandbox.py`. A container has no
  network access and opens only files whose access list admits it. The
  Windows and Program Files trees admit every container, so the system, LLVM
  and the Build Tools stay usable. On first use, the engine creates two
  container profiles, `omega_transform.evaluator` and
  `omega_transform.candidate`, and denies them the profile folder and registry
  key Windows gives each profile. It grants read access to the Python
  installation that runs the engine and to the `omega_transform` package, and
  evaluators also to each problem directory. These grants stay in place. Each
  job directory gets the low integrity label and is open to that job's
  processes alone; TEMP and TMP point into it. Containers get no window
  station, so nothing in them can use `user32.dll` (windows, the clipboard,
  the screen). Python's `ctypes` links it only through one unused COM
  function from `ole32.dll`; containers therefore load a copy of `_ctypes`
  that takes that function from `combase.dll` instead. The copy is made once,
  under `%LOCALAPPDATA%\omega_transform\ctypes`. Python's OpenSSL modules
  (`ssl`, and the OpenSSL algorithms of `hashlib`) do not load in a container;
  `hashlib` falls back to its own SHA-2, SHA-3, BLAKE2 and MD5.
  `multiprocessing` works: a container may create named pipes only in its
  own namespace, so the sandbox moves multiprocessing's pipes under
  `\\.\pipe\LOCAL\` (`omega_transform/wincontainer.py`, applied through a
  `sitecustomize.py` that the sandbox puts first on PYTHONPATH). A Python
  interpreter other than the one running the engine (`evaluator.python`)
  must be readable by containers too. Containers cannot ask Visual Studio's
  setup service where the C++ tools are, so the engine finds them with
  `vswhere` and passes them on in `VCToolsInstallDir`.
- Ollama for Windows splits a model that does not fit one GPU across several
  (two 11 GB cards hold a 16 GB model at a 32k context, with a little of it on
  the CPU). The memory guard counts only the part of a model that does not fit
  into the GPUs' free memory against the free RAM (see
  [Robustness and safety](robustness.md)).

## Install

The source is on [GitHub](https://github.com/cklukas/OMEGA_TRANSFORM):

```bash
git clone https://github.com/cklukas/OMEGA_TRANSFORM.git
```

```bash
cd OMEGA_TRANSFORM
```

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

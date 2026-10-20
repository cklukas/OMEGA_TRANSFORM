<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Development

## Layout

| Path | Contents |
|---|---|
| `omega_transform/engine.py` | The search loop: workers, LLM and evaluation slots, retries, sweeps, recording |
| `omega_transform/database.py` | SQLite store of every attempt; islands, MAP-Elites archive, selection |
| `omega_transform/prompt.py` | Turns the history into the prompt; journal lines; notebook prompt |
| `omega_transform/blocks.py` | Editable regions; parsing and applying answers, with salvage |
| `omega_transform/sweep.py` | Parameter sweeps: parsing, substitution, planning, summaries |
| `omega_transform/policy.py` | Adaptive choice of prompt style and edit mode |
| `omega_transform/evaluator.py` | Jobs: build, cascade, sandbox, timeouts, memory limit |
| `omega_transform/llm.py` | Ollama and OpenAI-compatible clients, streaming, runaway guard |
| `omega_transform/bench.py`, `report.py` | Model benchmark and its documentation page |
| `omega_transform/replay.py` | Re-sending logged prompts with other settings |
| `omega_transform/docgen.py` | Generates the configuration and command-line pages |
| `examples/` | Problems: circle packing (Python), matmul (C++), toy (tests) |
| `bench/results/` | Committed benchmark results; the source of the benchmark page |
| `docs/` | This site, built by ckdocs |

The engine uses only the Python standard library, and should stay that way:
it runs on any machine with Python 3.11 and needs no environment.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests use a scripted model instead of Ollama, so they run in seconds and
need no server. The sandbox tests run where a sandbox is available (macOS,
Linux with bubblewrap) and are skipped on Windows. The suite passes on macOS
and Windows; Windows-only code (Job Objects, named pipes) lives in
`omega_transform/winjob.py`.

One test also evaluates every example's starting program with its own
evaluator, building the C/C++ ones. It takes a minute or two, so it runs
only when asked; CI always asks:

```bash
OMEGA_EXAMPLE_TESTS=1 python3 -m unittest discover -s tests
```

## Documentation

The site is built with `ckdocs` from `docs/`, configured by `ckdocs.yml`.
Two pages are generated from the code and must be regenerated after a change
to a setting or a command; a test fails otherwise:

```bash
python3 -m omega_transform docs
```

The benchmark page is generated from `bench/results/`:

```bash
python3 -m omega_transform report
```

To preview the site:

```bash
ckdocs serve --root .
```

## Continuous integration

`.ckgit/ci.yml` runs on every push to `main` on the ck-git-hosting server: it
runs the tests, regenerates the benchmark page from the committed results,
builds the site with `ckdocs --strict`, and publishes it as the project's
Pages site.

The test step first prints `omega sandbox`, which says whether jobs run
confined there. A CI step is itself a sandbox, so the sandbox tests need two
things from it: the mount API that bubblewrap uses (the ck-git-hosting CI
runner allows it from commit 350c60c on; its unit used to set
`RestrictSUIDSGID=yes`, whose seccomp filter refused it), and either netlink
sockets or an empty network namespace, which a CI step has. Where a sandbox
cannot be built, the sandbox tests are skipped and the reason is printed.

## Publishing to GitHub

Development happens on the private ck-git server with the full history. GitHub
receives a public history of its own that starts with one snapshot commit (the
first public version); `scripts/publish-github.sh` replays every later commit
of `main` on top of it, with its message, author and date, so GitHub's files
always equal those of `main`:

```bash
scripts/publish-github.sh -n
```

```bash
scripts/publish-github.sh
```

The first command shows what would be published, the second publishes it. The
script keeps the public history on a local branch `github`, which the `github`
remote pushes to GitHub's `main`, without tags. A `pre-push` hook, installed
by `scripts/publish-github.sh guard`, refuses any push to GitHub whose history
does not start at the snapshot commit, so `git push github main` or a merge of
the two histories cannot publish the private one by mistake.

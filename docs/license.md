<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Status and license

## Project status

OMEGA TRANSFORM is a short research prototype. It was built and tested on
the examples in this repository; it has not yet been used on any real
project. Expect rough edges, changing configuration keys and command-line
options, and results that depend strongly on the model in use. Check every
program it produces yourself before relying on it.

## License

Copyright (c) 2026 by C. Klukas. The project uses three licenses, one for
each kind of code it contains. Every file names its license in an
`SPDX-License-Identifier` line.

| Part | License | Text |
|---|---|---|
| The framework: `omega_transform/`, tests, scripts, documentation | GPL-3.0-only | `LICENSE` |
| The evaluator interface: `omega_transform/harness.py`, `omega_transform/candidate_runner.py` | LGPL-3.0-only | `COPYING.LESSER`, on top of `LICENSE` |
| The examples: `examples/` | MIT | `examples/LICENSE` |

Why three, and what this means for using the project:

- **The framework is GPL.** The engine stays free software: anyone who
  distributes a modified version of it, or a product built on it, has to
  pass on the source of that version under the same terms. Using the engine
  on your own machine, privately or at work, carries no such duty.
- **The programs it optimizes stay yours.** The engine only runs your
  evaluator and edits the program you give it; the GPL does not reach that
  program or the optimized versions it produces. Their license is whatever
  it was before.
- **The interface is LGPL.** An evaluator imports `omega_transform.harness`
  to call the candidate program. The LGPL lets that evaluator, and the
  problem it belongs to, carry any license (including a closed one), while
  changes to the two interface files themselves remain free software.
- **The examples are MIT.** They are meant as starting points: copy one,
  adapt it to your own problem, and license the result as you like, keeping
  only the copyright notice.

This summary is meant as orientation; the license texts are what counts.

The license texts are in the root of the repository (`LICENSE`,
`COPYING.LESSER`) and in `examples/LICENSE`.

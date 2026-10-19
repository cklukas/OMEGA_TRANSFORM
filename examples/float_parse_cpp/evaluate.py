# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the single-threaded decimal-to-double parser, scored by CPU time.

Stage "check": texts of 0, 1, 7, 30, 1001 and 20011 numbers (hard cases first),
correctness only.
Stage "bench": a text of 2'000'000 numbers (about 28 MB), best of 7 runs, timed with
the process CPU clock; score = MB of text parsed per CPU-second.
Usage: python evaluate.py <binary> <stage>
"""
import json
import sys

from omega_transform.harness import Candidate

BENCH_COUNT = 2_000_000
# The first lines of every text: the hard cases at the start of EDGE in harness.cpp.
TEXT_HEAD = ["0", "-0", "1", "-1.5", "0.1", "1e23", "9007199254740993", "4.9e-324"]


def run(binary, count, reps):
    # The binary runs in the candidate's sandbox, where it cannot read this directory.
    out = Candidate(binary).run([binary, str(count), str(reps)], timeout=300)
    if out.timed_out:
        raise RuntimeError(f"{count} numbers: the program exceeded 300 s")
    if out.code != 0:
        raise RuntimeError(f"{count} numbers: exit code {out.code}\n{out.stderr[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def mismatch(r):
    return f"wrong result for a text of {r['count']} numbers: {r['what']} (std::strtod)"


def main(binary, stage):
    try:
        if stage == "check":
            for count in (0, 1, 7, 30, 1001, 20011):
                r = run(binary, count, 1)
                if not r["ok"]:
                    print(json.dumps({"valid": False, "feedback": mismatch(r)}))
                    return
            print(json.dumps({"score": 0.0, "metrics": {}}))
            return
        r = run(binary, BENCH_COUNT, 7)
    except (RuntimeError, ValueError, KeyError) as e:
        print(json.dumps({"valid": False, "feedback": str(e)[-1500:]}))
        return
    if not r["ok"]:
        print(json.dumps({"valid": False, "feedback": mismatch(r)}))
        return
    print(json.dumps({"score": r["mb_per_s"],
                      "solution": {"kind": "parse", "count": BENCH_COUNT,
                                   "text_mb": round(r["bytes"] / 1e6, 2), "head": TEXT_HEAD},
                      "metrics": {"mb_per_cpu_s": r["mb_per_s"],
                                  "ns_per_number": r["ns_per_number"],
                                  "cpu_seconds": r["cpu_seconds"],
                                  "text_mb": r["bytes"] / 1e6}}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

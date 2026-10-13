# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the matmul kernel.

Stage "check": small odd sizes for correctness (fast, runs in parallel with others).
Stage "bench": n = 1024 timing, best of 3 (exclusive, nothing else runs meanwhile).
Usage: python evaluate.py <binary> <stage>
"""
import json
import sys

from omega_transform.harness import Candidate

TOL_PER_N = 2e-6  # allowed |error| per unit of n (float accumulation)


def run(binary, n, reps):
    # The binary runs in the candidate's sandbox, where it cannot read this directory.
    out = Candidate(binary).run([binary, str(n), str(reps)], timeout=300)
    if out.timed_out:
        raise RuntimeError(f"n={n}: the program exceeded 300 s")
    if out.code != 0:
        raise RuntimeError(f"n={n}: exit code {out.code}\n{out.stderr[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main(binary, stage):
    try:
        if stage == "check":
            worst = 0.0
            for n in (1, 7, 64, 131, 257):
                r = run(binary, n, 1)
                if not r["max_err"] <= TOL_PER_N * n + 1e-5:
                    print(json.dumps({"valid": False, "feedback":
                          f"wrong result for n={n}: max abs error {r['max_err']:.3g}"}))
                    return
                worst = max(worst, r["max_err"])
            print(json.dumps({"score": 0.0, "metrics": {"max_err_small": worst}}))
            return
        r = run(binary, 1024, 3)
    except (RuntimeError, ValueError, KeyError) as e:
        print(json.dumps({"valid": False, "feedback": str(e)[-1500:]}))
        return
    if not r["max_err"] <= TOL_PER_N * 1024 + 1e-5:
        print(json.dumps({"valid": False,
                          "feedback": f"wrong result for n=1024: max abs error {r['max_err']:.3g}"}))
        return
    print(json.dumps({"score": r["gflops"],
                      "metrics": {"gflops": r["gflops"], "seconds_n1024": r["seconds"],
                                  "max_err": r["max_err"]}}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

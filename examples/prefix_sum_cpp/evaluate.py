# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the single-threaded prefix sum + histogram kernel, scored by CPU time.

Stage "check": n = 0, 1, 7, 1001, 65537 for correctness.
Stage "bench": n = 10'000'000, best of 30 runs, timed with the process CPU clock;
score = GB of input (8 bytes per element) processed per CPU-second.
Usage: python evaluate.py <binary> <stage>
"""
import json
import sys

from omega_transform.harness import Candidate

BENCH_N = 10_000_000


def run(binary, n, reps):
    # The binary runs in the candidate's sandbox, where it cannot read this directory.
    out = Candidate(binary).run([binary, str(n), str(reps)], timeout=300)
    if out.timed_out:
        raise RuntimeError(f"n={n}: the program exceeded 300 s")
    if out.code != 0:
        raise RuntimeError(f"n={n}: exit code {out.code}\n{out.stderr[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def input_head(k):
    """The first k input values, generated exactly as harness.cpp does."""
    s, out = 0x853C49E6748FEA9B, []
    run_byte = 0
    mask = (1 << 64) - 1

    def rnd():
        nonlocal s
        s = (s * 6364136223846793005 + 1442695040888963407) & mask
        return s >> 16
    for _ in range(k):
        r = rnd()
        v = (r & 0xFFFFFFFF) - 2147483648
        kind = (r >> 32) & 7
        if kind < 4:
            v &= ~255
        elif kind < 6:
            if ((r >> 36) & 63) == 0:
                run_byte = rnd() & 255
            v = (v & ~255) | run_byte
        out.append(v)
    return out


def mismatch(r):
    return f"wrong result for n={r['n']}: {r['what']}"


def main(binary, stage):
    try:
        if stage == "check":
            for n in (0, 1, 7, 1001, 65537):
                r = run(binary, n, 1)
                if not r["ok"]:
                    print(json.dumps({"valid": False, "feedback": mismatch(r)}))
                    return
            print(json.dumps({"score": 0.0, "metrics": {}}))
            return
        r = run(binary, BENCH_N, 30)
    except (RuntimeError, ValueError, KeyError) as e:
        print(json.dumps({"valid": False, "feedback": str(e)[-1500:]}))
        return
    if not r["ok"]:
        print(json.dumps({"valid": False, "feedback": mismatch(r)}))
        return
    print(json.dumps({"score": r["gb_per_s"],
                      "solution": {"kind": "scan", "n": BENCH_N, "head": input_head(6)},
                      "metrics": {"gb_per_cpu_s": r["gb_per_s"],
                                  "ns_per_element": r["ns_per_element"],
                                  "cpu_seconds_n10m": r["cpu_seconds"]}}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the single-threaded uint32 sort, scored by CPU time.

Stage "check": small and odd sizes (0, 1, 2, 7, 100, 1001, 65537) for correctness.
Stage "bench": n = 1'000'000 for each of 5 input distributions, best of 5 runs each,
timed with the process CPU clock; score = total throughput (all elements divided by
the total CPU time) in million elements per CPU-second.
Usage: python evaluate.py <binary> <stage>
"""
import json
import sys

from omega_transform.harness import Candidate

BENCH_N = 1_000_000


def run(binary, n, reps):
    # The binary runs in the candidate's sandbox, where it cannot read this directory.
    out = Candidate(binary).run([binary, str(n), str(reps)], timeout=300)
    if out.timed_out:
        raise RuntimeError(f"n={n}: the program exceeded 300 s")
    if out.code != 0:
        raise RuntimeError(f"n={n}: exit code {out.code}\n{out.stderr[-1500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def mismatch(r):
    msg = (f"wrong result for n={r['n']}, distribution '{r['dist']}': at index "
           f"{r['index']} got {r['got']}, expected {r['expected']} (std::sort)")
    if r.get("note"):
        msg += f"; {r['note']}"
    return msg


def main(binary, stage):
    try:
        if stage == "check":
            for n in (0, 1, 2, 7, 100, 1001, 65537):
                r = run(binary, n, 1)
                if not r["ok"]:
                    print(json.dumps({"valid": False, "feedback": mismatch(r)}))
                    return
            print(json.dumps({"score": 0.0, "metrics": {}}))
            return
        r = run(binary, BENCH_N, 5)
    except (RuntimeError, ValueError, KeyError) as e:
        print(json.dumps({"valid": False, "feedback": str(e)[-1500:]}))
        return
    if not r["ok"]:
        print(json.dumps({"valid": False, "feedback": mismatch(r)}))
        return
    # Total throughput: all elements of all distributions over the total CPU time
    # (the harmonic mean of the rates, as every distribution has the same n). Unlike
    # a geometric mean, trivial inputs that finish almost instantly (already sorted)
    # cannot dominate it; the slow, real sorting work does.
    rates = {name: d["melem_per_s"] for name, d in r["dists"].items()}
    total = len(rates) / sum(1.0 / max(v, 1e-9) for v in rates.values())
    metrics = {"melem_per_cpu_s_total": total, "melem_per_cpu_s_geomean": r["geomean_melem_per_s"]}
    for name, v in rates.items():
        metrics[f"melem_per_cpu_s_{name}"] = v
    print(json.dumps({"score": total, "metrics": metrics}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

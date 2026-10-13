# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for weighted max-cut: validates the side assignment and scores the cut fraction.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)

Three fixed random graphs with integer edge weights 1..10 are generated from fixed seeds,
identically on every run. For each graph the candidate's cut(n, edges) must return a 0/1
side per node within the CPU time limit. score = mean over the graphs of
(cut weight / total edge weight). Higher is better; a random split gets about 0.5.
"""
import json
import operator
import random
import sys

from omega_transform.harness import Candidate, CandidateError

INSTANCES = [("g100", 100, 400, 11), ("g200", 200, 1200, 12), ("g400", 400, 1600, 13)]  # name, nodes, edges, seed
TIME_LIMIT = 2.0   # CPU seconds per graph
HARD_FACTOR = 1.5  # more than HARD_FACTOR * TIME_LIMIT makes the candidate invalid


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def make_graph(n, m, seed):
    """m distinct random edges (u, v, w) with u < v and integer weight 1..10."""
    rng = random.Random(seed)
    edges = {}
    while len(edges) < m:
        u, v = rng.randrange(n), rng.randrange(n)
        if u == v:
            continue
        if u > v:
            u, v = v, u
        if (u, v) not in edges:
            edges[(u, v)] = rng.randint(1, 10)
    return [(u, v, w) for (u, v), w in edges.items()]


def validate(name, n, side):
    if len(side) != n:
        fail(f"{name}: cut() returned {len(side)} sides, expected one per node ({n})")
    out = []
    for i, s in enumerate(side):
        try:
            s = operator.index(s)
        except TypeError:
            fail(f"{name}: side[{i}] = {s!r} is not an int 0 or 1")
        if s not in (0, 1):
            fail(f"{name}: side[{i}] = {s} is not 0 or 1")
        out.append(s)
    return out


def main(path):
    cand = Candidate(path)
    metrics, ratios, used, balance = {}, [], [], []
    for name, n, m, seed in INSTANCES:
        edges = make_graph(n, m, seed)
        try:
            side = cand.call("cut", n, list(edges))
        except CandidateError as e:
            if e.message == "the program defines no function 'cut'":
                fail("the program must define cut(n, edges)")
            fail(f"{name}: cut() failed: {e}")
        secs = cand.last_cpu  # CPU time of this call, measured in the candidate's process
        if secs > HARD_FACTOR * TIME_LIMIT:
            fail(f"{name}: cut() used {secs:.2f} CPU s, limit is {TIME_LIMIT} s "
                 f"(hard cut-off {HARD_FACTOR * TIME_LIMIT:.1f} s); check time.process_time() "
                 f"more often and stop earlier")
        if side is None:
            fail(f"{name}: cut() returned None")
        try:
            side = list(side)
        except TypeError:
            fail(f"{name}: cut() must return a list of n sides")
        side = validate(name, n, side)
        total = sum(w for _, _, w in edges)
        weight = sum(w for u, v, w in edges if side[u] != side[v])
        ratios.append(weight / total)
        used.append(secs / TIME_LIMIT)
        balance.append(abs(sum(side) / n - 0.5) * 2)
        metrics[f"{name}_cut"] = weight
        metrics[f"{name}_total"] = total
        metrics[f"{name}_ratio"] = round(weight / total, 6)
        metrics[f"{name}_cpu_s"] = round(secs, 3)
    score = sum(ratios) / len(ratios)
    worst = min(range(len(ratios)), key=ratios.__getitem__)
    print(json.dumps({
        "score": score,
        "metrics": metrics,
        "features": {"time_used": sum(used) / len(used), "imbalance": sum(balance) / len(balance)},
        "valid": True,
        "feedback": f"weakest graph: {INSTANCES[worst][0]} cuts {ratios[worst]:.4f} of its edge weight",
    }))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: evaluate.py <candidate.py>")
    main(sys.argv[1])

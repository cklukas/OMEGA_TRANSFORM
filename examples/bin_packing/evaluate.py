# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for 1D bin packing: validates the packing and scores bins used against the lower bound.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)

Three fixed instances with integer item sizes and capacity 1000 are generated from fixed
seeds, identically on every run. For each instance the candidate's pack(sizes, capacity)
must return a list of bins (lists of item indices) within the CPU time limit.
score = mean over the instances of (ceil(sum(sizes) / capacity) / bins used). Higher is
better; 1.0 means every instance meets the trivial lower bound (which may not be
reachable on every instance).
"""
import json
import math
import operator
import random
import sys

from omega_transform.harness import Candidate, CandidateError

CAPACITY = 1000
# name, number of items, seed, smallest size, largest size
INSTANCES = [
    ("u300", 300, 1, 100, 700),
    ("u1000", 1000, 4, 150, 450),
    ("t1000", 1000, 3, 250, 500),  # mostly three items per bin: hard for simple greedy rules
]
TIME_LIMIT = 2.0   # CPU seconds per instance
HARD_FACTOR = 1.5  # more than HARD_FACTOR * TIME_LIMIT makes the candidate invalid


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def make_sizes(n, seed, lo, hi):
    rng = random.Random(seed)
    return [rng.randint(lo, hi) for _ in range(n)]


def validate(name, sizes, bins):
    n = len(sizes)
    where = [None] * n
    try:
        bins = [[operator.index(i) for i in b] for b in bins]
    except TypeError:
        fail(f"{name}: pack() must return a list of bins, each a list of int item indices")
    for b, items in enumerate(bins):
        if not items:
            fail(f"{name}: bin {b} is empty; return only bins that hold items")
        load = 0
        for i in items:
            if not 0 <= i < n:
                fail(f"{name}: bin {b} holds {i}, which is not an item index (0..{n - 1})")
            if where[i] is not None:
                fail(f"{name}: item {i} is packed twice (bins {where[i]} and {b})")
            where[i] = b
            load += sizes[i]
        if load > CAPACITY:
            fail(f"{name}: bin {b} holds items of total size {load} > capacity {CAPACITY}")
    for i in range(n):
        if where[i] is None:
            missing = sum(1 for w in where if w is None)
            fail(f"{name}: item {i} (size {sizes[i]}) is not packed ({missing} items missing)")
    return bins


def main(path):
    cand = Candidate(path)
    metrics, ratios, used, fills = {}, [], [], []
    solution = None
    for name, n, seed, lo, hi in INSTANCES:
        sizes = make_sizes(n, seed, lo, hi)
        try:
            bins = cand.call("pack", list(sizes), CAPACITY)
        except CandidateError as e:
            if e.message == "the program defines no function 'pack'":
                fail("the program must define pack(sizes, capacity)")
            fail(f"{name}: pack() failed: {e}")
        secs = cand.last_cpu  # CPU time of this call, measured in the candidate's process
        if secs > HARD_FACTOR * TIME_LIMIT:
            fail(f"{name}: pack() used {secs:.2f} CPU s, limit is {TIME_LIMIT} s "
                 f"(hard cut-off {HARD_FACTOR * TIME_LIMIT:.1f} s); check time.process_time() "
                 f"more often and stop earlier")
        if bins is None:
            fail(f"{name}: pack() returned None")
        try:
            bins = list(bins)
        except TypeError:
            fail(f"{name}: pack() must return a list of bins")
        bins = validate(name, sizes, bins)
        if solution is None:  # the first instance is drawn on the example page
            solution = {"kind": "bins", "instance": name, "capacity": CAPACITY,
                        "lower_bound": math.ceil(sum(sizes) / CAPACITY),
                        "bins": [[sizes[i] for i in b] for b in bins]}
        lower = math.ceil(sum(sizes) / CAPACITY)
        ratios.append(lower / len(bins))
        used.append(secs / TIME_LIMIT)
        fills.append(sum(sizes) / (len(bins) * CAPACITY))
        metrics[f"{name}_bins"] = len(bins)
        metrics[f"{name}_lower_bound"] = lower
        metrics[f"{name}_ratio"] = round(lower / len(bins), 6)
        metrics[f"{name}_cpu_s"] = round(secs, 3)
    score = sum(ratios) / len(ratios)
    gaps = ", ".join(f"{name}: {metrics[name + '_bins'] - metrics[name + '_lower_bound']}"
                     for name, *_ in INSTANCES)
    print(json.dumps({
        "score": score,
        "metrics": metrics,
        "features": {"time_used": sum(used) / len(used), "mean_fill": sum(fills) / len(fills)},
        "solution": solution,
        "valid": True,
        "feedback": f"bins above the lower bound per instance: {gaps}",
    }))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: evaluate.py <candidate.py>")
    main(sys.argv[1])

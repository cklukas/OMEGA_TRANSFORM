# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the TSP heuristic: validates the tours and scores them against nearest neighbour.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)

Three fixed instances (200, 500 and 1000 uniform random cities in the unit square) are
generated from fixed seeds, identically on every run. For each instance the candidate's
solve(points, time_limit) must return a permutation of all city indices within the CPU
time limit. score = mean over the instances of (nearest-neighbour tour length / candidate
tour length); the nearest-neighbour tour starts at city 0, so 1.0 means "as good as nearest
neighbour" and higher is better (good heuristics reach about 1.2 to 1.25).
"""
import json
import math
import operator
import random
import sys

from omega_transform.harness import Candidate, CandidateError

INSTANCES = [("n200", 200, 1001), ("n500", 500, 1002), ("n1000", 1000, 1003)]  # name, n, seed
TIME_LIMIT = 2.0   # CPU seconds per instance, passed to solve()
HARD_FACTOR = 1.5  # more than HARD_FACTOR * TIME_LIMIT makes the candidate invalid


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def make_points(n, seed):
    rng = random.Random(seed)
    return [(rng.random(), rng.random()) for _ in range(n)]


def tour_length(points, tour):
    total = 0.0
    px, py = points[tour[-1]]
    for c in tour:
        x, y = points[c]
        total += math.hypot(x - px, y - py)
        px, py = x, y
    return total


def nearest_neighbour_length(points):
    """Length of the nearest-neighbour tour from city 0 (lowest index wins ties)."""
    n = len(points)
    left = list(range(1, n))
    tour = [0]
    x, y = points[0]
    while left:
        best_k, best_d = 0, float("inf")
        for k, j in enumerate(left):
            qx, qy = points[j]
            d = (qx - x) * (qx - x) + (qy - y) * (qy - y)
            if d < best_d:
                best_k, best_d = k, d
        j = left[best_k]
        left[best_k] = left[-1]
        left.pop()
        tour.append(j)
        x, y = points[j]
    return tour_length(points, tour)


def validate(name, n, tour):
    try:
        tour = [operator.index(c) for c in tour]
    except TypeError:
        fail(f"{name}: solve() must return a sequence of int city indices")
    if len(tour) != n:
        fail(f"{name}: tour has {len(tour)} entries, expected {n} (each city exactly once)")
    seen = [False] * n
    for pos, c in enumerate(tour):
        if not 0 <= c < n:
            fail(f"{name}: tour[{pos}] = {c} is not a city index (0..{n - 1})")
        if seen[c]:
            present = set(tour)
            missing = next(k for k in range(n) if k not in present)
            fail(f"{name}: city {c} visited twice (at position {pos}); city {missing} is missing")
        seen[c] = True
    return tour


def main(path):
    cand = Candidate(path)
    metrics, ratios, used = {}, [], []
    for name, n, seed in INSTANCES:
        points = make_points(n, seed)
        try:
            tour = cand.call("solve", list(points), TIME_LIMIT)
        except CandidateError as e:
            if e.message == "the program defines no function 'solve'":
                fail("the program must define solve(points, time_limit)")
            fail(f"{name}: solve() failed: {e}")
        secs = cand.last_cpu  # CPU time of this call, measured in the candidate's process
        if secs > HARD_FACTOR * TIME_LIMIT:
            fail(f"{name}: solve() used {secs:.2f} CPU s, limit is {TIME_LIMIT} s "
                 f"(hard cut-off {HARD_FACTOR * TIME_LIMIT:.1f} s); check time.process_time() "
                 f"more often and stop earlier")
        if tour is None:
            fail(f"{name}: solve() returned None")
        tour = validate(name, n, list(tour))
        length = tour_length(points, tour)
        ref = nearest_neighbour_length(points)
        ratios.append(ref / length)
        used.append(secs / TIME_LIMIT)
        metrics[f"{name}_length"] = round(length, 6)
        metrics[f"{name}_nn_length"] = round(ref, 6)
        metrics[f"{name}_ratio"] = round(ref / length, 6)
        metrics[f"{name}_cpu_s"] = round(secs, 3)
    score = sum(ratios) / len(ratios)
    worst = min(range(len(ratios)), key=ratios.__getitem__)
    print(json.dumps({
        "score": score,
        "metrics": metrics,
        "features": {"time_used": sum(used) / len(used)},
        "valid": True,
        "feedback": f"weakest instance: {INSTANCES[worst][0]} at {ratios[worst]:.4f} x nearest neighbour",
    }))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: evaluate.py <candidate.py>")
    main(sys.argv[1])

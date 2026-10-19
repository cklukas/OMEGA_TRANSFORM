# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for circle packing: validates the packing and scores the sum of radii.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)
"""
import json
import math
import sys

from omega_transform.harness import Candidate, CandidateError

N = 26
BEST_KNOWN = 2.635  # best known value for n = 26
TOL = 1e-9


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def main(path):
    cand = Candidate(path)
    try:
        circles = cand.call("run")
    except CandidateError as e:
        fail(str(e))
    secs = cand.last_cpu
    if not isinstance(circles, list):
        fail(f"run() must return a list of {N} (x, y, r) triples, got {type(circles).__name__}")
    if len(circles) != N:
        fail(f"expected {N} circles, got {len(circles)}")
    try:
        circles = [(float(x), float(y), float(r)) for x, y, r in circles]
    except (TypeError, ValueError):
        fail("each circle must be a triple of numbers (x, y, r)")
    problems = []
    for k, (x, y, r) in enumerate(circles):
        if not all(map(math.isfinite, (x, y, r))) or r < 0:
            problems.append(f"circle {k} has invalid values {(x, y, r)}")
        elif x - r < -TOL or y - r < -TOL or x + r > 1 + TOL or y + r > 1 + TOL:
            problems.append(f"circle {k} at ({x:.4f},{y:.4f}) r={r:.4f} leaves the square")
    for a in range(N):
        for b in range(a + 1, N):
            xa, ya, ra = circles[a]
            xb, yb, rb = circles[b]
            gap = math.hypot(xa - xb, ya - yb) - ra - rb
            if gap < -TOL:
                problems.append(f"circles {a} and {b} overlap by {-gap:.2e}")
    if problems:
        fail(f"{len(problems)} constraint violations, e.g.: " + "; ".join(problems[:4]))
    total = sum(r for _, _, r in circles)
    radii = sorted(r for _, _, r in circles)
    print(json.dumps({
        "score": total,
        "metrics": {"sum_radii": total, "ratio_to_best_known": total / BEST_KNOWN,
                    "min_radius": radii[0], "max_radius": radii[-1], "seconds": secs},
        "features": {"radius_spread": radii[-1] - radii[0], "seconds": secs},
        "solution": {"kind": "circles", "container": "square",
                     "circles": [[round(v, 6) for v in c] for c in circles]},
    }))


if __name__ == "__main__":
    main(sys.argv[1])

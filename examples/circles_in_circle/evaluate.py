# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for circles in a circle: validates 21 circles inside the unit circle and
scores the sum of radii.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)
"""
import json
import math
import sys

from omega_transform.harness import Candidate, CandidateError

N = 21
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
        elif math.hypot(x, y) + r > 1 + TOL:
            problems.append(f"circle {k} at ({x:.4f},{y:.4f}) r={r:.4f} leaves the unit circle "
                            f"by {math.hypot(x, y) + r - 1:.2e}")
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
    density = sum(r * r for r in radii)  # covered fraction of the unit disk
    print(json.dumps({
        "score": total,
        "metrics": {"sum_radii": total, "min_radius": radii[0], "max_radius": radii[-1],
                    "area_fraction": density, "seconds": secs},
        "features": {"radius_spread": radii[-1] - radii[0], "area_fraction": density},
        "valid": True,
        "feedback": f"sum of radii {total:.6f}; radii range {radii[0]:.4f} to {radii[-1]:.4f}; "
                    f"circles cover {100 * density:.1f}% of the container",
    }))


if __name__ == "__main__":
    main(sys.argv[1])

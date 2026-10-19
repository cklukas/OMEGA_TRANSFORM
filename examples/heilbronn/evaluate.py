# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the Heilbronn triangle problem: validates 13 points in the unit
square and scores the smallest triangle area.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)
"""
import itertools
import json
import math
import sys

from omega_transform.harness import Candidate, CandidateError

N = 13
TOL = 1e-12          # points may lie this far outside [0,1]^2
NEAR_MIN_REL = 1e-6  # triangles within this relative distance of the minimum count as near-minimal


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def main(path):
    cand = Candidate(path)
    try:
        points = cand.call("run")
    except CandidateError as e:
        fail(str(e))
    secs = cand.last_cpu
    if not isinstance(points, list):
        fail(f"run() must return a list of {N} (x, y) pairs, got {type(points).__name__}")
    if len(points) != N:
        fail(f"expected {N} points, got {len(points)}")
    try:
        points = [(float(x), float(y)) for x, y in points]
    except (TypeError, ValueError):
        fail("each point must be a pair of numbers (x, y)")
    problems = []
    for k, (x, y) in enumerate(points):
        if not (math.isfinite(x) and math.isfinite(y)):
            problems.append(f"point {k} has invalid values {(x, y)}")
        elif x < -TOL or y < -TOL or x > 1 + TOL or y > 1 + TOL:
            problems.append(f"point {k} at ({x:.6g},{y:.6g}) lies outside the unit square")
    if problems:
        fail(f"{len(problems)} constraint violations, e.g.: " + "; ".join(problems[:4]))
    areas = []
    for a, b, c in itertools.combinations(range(N), 3):
        (xa, ya), (xb, yb), (xc, yc) = points[a], points[b], points[c]
        areas.append((0.5 * abs((xb - xa) * (yc - ya) - (xc - xa) * (yb - ya)), (a, b, c)))
    areas.sort()
    min_area, worst = areas[0]
    near = sum(1 for s, _ in areas if s <= min_area * (1 + NEAR_MIN_REL) + 1e-15)
    min_dist = min(math.dist(p, q) for p, q in itertools.combinations(points, 2))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    hull_box = (max(xs) - min(xs)) * (max(ys) - min(ys))
    feedback = (f"smallest triangle is points {worst} with area {min_area:.6g}; "
                f"{near} triangle(s) within {NEAR_MIN_REL:g} of the minimum; "
                f"next smallest areas: " + ", ".join(f"{s:.4g}" for s, _ in areas[1:5]))
    print(json.dumps({
        "score": min_area,
        "metrics": {"min_area": min_area, "normalized_min_area_n2": min_area * N * N,
                    "near_min_triangles": near, "min_point_distance": min_dist,
                    "bounding_box_area": hull_box, "seconds": secs},
        "features": {"near_min_triangles": near, "min_point_distance": min_dist},
        "solution": {"kind": "heilbronn", "points": [[x, y] for x, y in points]},
        "valid": True,
        "feedback": feedback,
    }))


if __name__ == "__main__":
    main(sys.argv[1])

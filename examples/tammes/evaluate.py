# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the Tammes problem: normalizes 30 vectors onto the unit sphere and
scores the minimum pairwise angle in degrees.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)
"""
import itertools
import json
import math
import sys

from omega_transform.harness import Candidate, CandidateError

N = 30
MIN_NORM = 1e-9      # shorter vectors are rejected as (nearly) zero
NEAR_MIN_DEG = 1e-6  # pairs within this many degrees of the minimum count as contacts


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def angle_deg(p, q):
    # atan2 of |p x q| and p.q is accurate also for small angles
    cx = p[1] * q[2] - p[2] * q[1]
    cy = p[2] * q[0] - p[0] * q[2]
    cz = p[0] * q[1] - p[1] * q[0]
    dot = p[0] * q[0] + p[1] * q[1] + p[2] * q[2]
    return math.degrees(math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot))


def main(path):
    cand = Candidate(path)
    try:
        points = cand.call("run")
    except CandidateError as e:
        fail(str(e))
    secs = cand.last_cpu
    if not isinstance(points, list):
        fail(f"run() must return a list of {N} (x, y, z) triples, got {type(points).__name__}")
    if len(points) != N:
        fail(f"expected {N} points, got {len(points)}")
    try:
        points = [(float(x), float(y), float(z)) for x, y, z in points]
    except (TypeError, ValueError):
        fail("each point must be a triple of numbers (x, y, z)")
    problems, unit = [], []
    for k, p in enumerate(points):
        norm = math.sqrt(sum(c * c for c in p)) if all(map(math.isfinite, p)) else float("nan")
        if not math.isfinite(norm):
            problems.append(f"point {k} has invalid values {p}")
        elif norm < MIN_NORM:
            problems.append(f"point {k} is a zero vector (norm {norm:.2e})")
        else:
            unit.append(tuple(c / norm for c in p))
    if problems:
        fail(f"{len(problems)} constraint violations, e.g.: " + "; ".join(problems[:4]))
    pairs = sorted((angle_deg(unit[a], unit[b]), (a, b))
                   for a, b in itertools.combinations(range(N), 2))
    min_angle, worst = pairs[0]
    contacts = sum(1 for ang, _ in pairs if ang <= min_angle + NEAR_MIN_DEG)
    nearest = [min(angle_deg(unit[a], unit[b]) for b in range(N) if b != a) for a in range(N)]
    mean_nn = sum(nearest) / N
    nn_spread = max(nearest) - min(nearest)
    feedback = (f"closest pair is points {worst} at {min_angle:.6f} degrees; {contacts} pair(s) "
                f"within {NEAR_MIN_DEG:g} degrees of the minimum; nearest-neighbor angles range "
                f"{min(nearest):.3f} to {max(nearest):.3f} degrees")
    print(json.dumps({
        "score": min_angle,
        "metrics": {"min_angle_deg": min_angle, "contact_pairs": contacts,
                    "mean_nearest_neighbor_deg": mean_nn, "nearest_neighbor_spread_deg": nn_spread,
                    "seconds": secs},
        "features": {"nearest_neighbor_spread_deg": nn_spread, "contact_pairs": contacts},
        "solution": {"kind": "sphere_points", "points": [list(p) for p in unit]},
        "valid": True,
        "feedback": feedback,
    }))


if __name__ == "__main__":
    main(sys.argv[1])

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Tammes problem (spherical code): place 30 points on the unit sphere so that the
smallest angle between any two of them is as large as possible."""
import math
import random

# OMEGA-BLOCK-START
def construct(n=30):
    """Return a list of n points (x, y, z) on the unit sphere.

    Vectors need not be normalized (the evaluator normalizes them), but none may be
    zero. The score is the minimum angle in degrees between any two points. This
    start draws uniformly random points with a fixed seed, so some pairs end up
    close together.
    """
    rng = random.Random(1)
    points = []
    for _ in range(n):
        z = rng.uniform(-1.0, 1.0)
        phi = rng.uniform(0.0, 2 * math.pi)
        s = math.sqrt(1.0 - z * z)
        points.append((s * math.cos(phi), s * math.sin(phi), z))
    return points
# OMEGA-BLOCK-END


def run():
    return construct(30)

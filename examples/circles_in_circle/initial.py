# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Pack 21 non-overlapping circles inside the unit circle, maximizing the sum of radii."""
import math

# OMEGA-BLOCK-START
def construct(n=21):
    """Return a list of n circles (x, y, r) inside the unit circle (center (0,0),
    radius 1) without overlaps.

    This start places one circle at the center and two concentric rings of 7 and
    13 circles, all with the same radius: the largest one that keeps every pair
    apart and every circle inside the container.
    """
    centers = [(0.0, 0.0)]
    for count, ring in ((7, 0.35), (n - 8, 0.75)):
        centers += [(ring * math.cos(2 * math.pi * k / count),
                     ring * math.sin(2 * math.pi * k / count)) for k in range(count)]
    r = min(1.0 - math.hypot(x, y) for x, y in centers)
    for a in range(n):
        for b in range(a + 1, n):
            r = min(r, math.dist(centers[a], centers[b]) / 2)
    r *= 1 - 1e-12  # stay strictly feasible despite rounding
    return [(x, y, r) for x, y in centers]
# OMEGA-BLOCK-END


def run():
    return construct(21)

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Pack 26 non-overlapping circles in the unit square, maximizing the sum of radii.

The best known values for n = 26 are about 2.635 to 2.6359.
"""
import math

# OMEGA-BLOCK-START
def construct_packing(n=26):
    """Return a list of n circles (x, y, r) inside the unit square without overlaps."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    centers = []
    for k in range(n):
        i, j = k % cols, k // cols
        centers.append(((i + 0.5) / cols, (j + 0.5) / rows))
    radii = max_radii(centers)
    return [(x, y, r) for (x, y), r in zip(centers, radii)]


def max_radii(centers):
    """Largest radii for fixed centers: limit by walls, then shrink overlapping pairs."""
    radii = [min(x, y, 1 - x, 1 - y) for x, y in centers]
    for a in range(len(centers)):
        for b in range(a + 1, len(centers)):
            d = math.dist(centers[a], centers[b])
            if radii[a] + radii[b] > d:
                s = d / (radii[a] + radii[b])
                radii[a] *= s
                radii[b] *= s
    return radii
# OMEGA-BLOCK-END


def run():
    return construct_packing(26)

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Heilbronn triangle problem: place 13 points in the unit square so that the
smallest triangle formed by any 3 of them has the largest possible area."""
import math

# OMEGA-BLOCK-START
def construct(n=13):
    """Return a list of n points (x, y) inside the closed unit square [0,1]^2.

    The score is the smallest area among all triangles with corners at three of
    the points, so no three points may be (nearly) collinear. This start puts
    the points on a regular polygon on a small circle around the center, which
    avoids collinear triples but wastes most of the square.
    """
    radius = 0.3
    return [(0.5 + radius * math.cos(2 * math.pi * k / n),
             0.5 + radius * math.sin(2 * math.pi * k / n)) for k in range(n)]
# OMEGA-BLOCK-END


def run():
    return construct(13)

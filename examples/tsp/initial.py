# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Euclidean traveling salesman heuristic: find a short closed tour through all cities.

The evaluator calls solve(points, time_limit) on three fixed instances (200, 500 and
1000 random cities in the unit square) and scores the tour lengths.
"""
import math
import random
import time

# OMEGA-BLOCK-START
def solve(points, time_limit):
    """Return a closed tour through all cities as a permutation of range(len(points)).

    points: list of (x, y) float tuples in the unit square; time_limit: CPU seconds
    allowed for this call (measured with time.process_time(); more than 1.5 x the limit
    makes the program invalid). The tour returns from the last city to the first.
    Only the Python standard library may be used.

    Current method: plain nearest-neighbour construction starting at city 0.
    """
    n = len(points)
    unvisited = set(range(1, n))
    tour = [0]
    x, y = points[0]
    while unvisited:
        nxt = min(unvisited, key=lambda j: (points[j][0] - x) ** 2 + (points[j][1] - y) ** 2)
        unvisited.remove(nxt)
        tour.append(nxt)
        x, y = points[nxt]
    return tour
# OMEGA-BLOCK-END

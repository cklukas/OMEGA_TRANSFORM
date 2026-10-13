# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Weighted max-cut: split the nodes of a graph into two sides to maximize the cut weight.

The evaluator calls cut(n, edges) on three fixed random graphs (100 to 400 nodes,
integer weights) and scores the weight of the edges between the two sides.
"""
import math
import random
import time

# OMEGA-BLOCK-START
def cut(n, edges):
    """Return a list of n values, side[i] in {0, 1} for node i.

    n: number of nodes (0..n-1); edges: list of (u, v, w) with 0 <= u < v < n, no
    duplicate pairs, integer weight 1 <= w <= 10. The cut weight is the sum of w over
    edges whose endpoints are on different sides; larger is better. The call has a CPU
    time limit of 2.0 s (time.process_time(); more than 1.5 x the limit makes the program
    invalid). Only the Python standard library may be used.

    Current method: alternate the sides by node index (ignores the edges entirely).
    """
    return [i % 2 for i in range(n)]
# OMEGA-BLOCK-END

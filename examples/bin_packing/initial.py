# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""One-dimensional bin packing: pack integer item sizes into as few bins as possible.

The evaluator calls pack(sizes, capacity) on three fixed instances (300 to 1000 items,
capacity 1000) and scores the number of bins used against the lower bound.
"""
import math
import random
import time

# OMEGA-BLOCK-START
def pack(sizes, capacity):
    """Return a list of bins; each bin is a list of item indices into sizes.

    sizes: list of ints, each between 1 and capacity; capacity: int bin capacity.
    Every item index must appear in exactly one bin and the sizes in a bin must sum
    to at most capacity. Fewer bins is better. The call has a CPU time limit of 2.0 s
    (time.process_time(); more than 1.5 x the limit makes the program invalid).
    Only the Python standard library may be used.

    Current method: next fit (open a new bin whenever the next item does not fit).
    """
    bins = []
    current, load = [], 0
    for i, s in enumerate(sizes):
        if load + s > capacity:
            bins.append(current)
            current, load = [], 0
        current.append(i)
        load += s
    if current:
        bins.append(current)
    return bins
# OMEGA-BLOCK-END

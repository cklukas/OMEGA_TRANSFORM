# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Symbolic regression: find the formula behind y = f(x1, x2, x3) + noise.

The evaluator calls fit(train_rows) once with 300 training rows (x1, x2, x3, y), then
predict(params, x1, x2, x3) on held-out rows, both inside and OUTSIDE the training
range of the inputs, and scores the R^2 of the predictions.
"""
import math


def least_squares(columns, targets, ridge=1e-9):
    """Fixed helper: coefficients c minimizing sum_i (sum_j c_j * columns[j][i] - targets[i])^2.

    columns: list of basis columns (each a list of floats, one value per row).
    Solves the normal equations by Gaussian elimination with partial pivoting.
    """
    k = len(columns)
    a = [[sum(p * q for p, q in zip(columns[r], columns[c])) + (ridge if r == c else 0.0)
          for c in range(k)] + [sum(p * t for p, t in zip(columns[r], targets))]
         for r in range(k)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        if abs(a[col][col]) < 1e-300:
            continue
        for r in range(k):
            if r != col:
                f = a[r][col] / a[col][col]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [a[r][k] / a[r][r] if abs(a[r][r]) > 1e-300 else 0.0 for r in range(k)]


# OMEGA-BLOCK-START
def basis(x1, x2, x3):
    """Basis functions of the model; the prediction is a weighted sum of them."""
    return [1.0, x1, x2, x3]


def fit(rows):
    """Fit the model on the training rows and return its parameters.

    The parameters must be plain data (numbers, strings, lists, dicts), not functions
    or class instances. predict() receives them as JSON would carry them: tuples become
    lists and dict keys become strings.
    rows: list of [x1, x2, x3, y] float lists, x1 in [-3, 3], x2 in [-2, 2],
    x3 in [0, 3]. Only the Python standard library may be used (math, statistics,
    random, ...; no numpy). fit plus all predictions must take at most 10 CPU seconds.

    Current method: linear regression on the raw inputs.
    """
    cols = list(zip(*[basis(x1, x2, x3) for x1, x2, x3, _ in rows]))
    return least_squares([list(c) for c in cols], [y for *_, y in rows])


def predict(params, x1, x2, x3):
    """Return the predicted y (a finite float) for one input."""
    return sum(c * b for c, b in zip(params, basis(x1, x2, x3)))
# OMEGA-BLOCK-END

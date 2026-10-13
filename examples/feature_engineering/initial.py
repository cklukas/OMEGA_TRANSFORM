# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Feature engineering for a fixed linear classifier: maximize held-out accuracy.

The evaluator maps every row of a fixed binary-classification dataset (6 raw numeric
inputs) through features(row), trains its own fixed linear classifier on 1000 rows,
and scores the accuracy on 1000 held-out rows. Only the features can be changed.
"""
import math

# OMEGA-BLOCK-START
def features(row):
    """Return the feature vector (a list of at most 30 finite floats) for one row.

    row: list of 6 floats [x1, x2, x3, x4, x5, x6]. Roughly: x1, x2, x6 are
    standard-normal-like, x3 is positive and right-skewed (lognormal-like), x4 and x5
    lie in [-2, 2]. The label is a hidden NONLINEAR function of the inputs plus noise.
    Every row must give the same number of features. The classifier is linear in the
    features (they are standardized and a bias is added), so nonlinear structure only
    helps if a feature expresses it. Only the Python standard library may be used.

    Current method: the raw inputs.
    """
    return list(row)
# OMEGA-BLOCK-END

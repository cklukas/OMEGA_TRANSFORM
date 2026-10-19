# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for feature engineering: a fixed linear classifier on candidate features.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)

The model is fixed: features are standardized with the training mean and standard
deviation, a bias column is added, and a ridge-regularized least-squares classifier
(targets -1/+1, normal equations solved by Gaussian elimination) is trained on 1000
rows. Score = accuracy of sign(prediction) on 1000 held-out rows.
"""
import json
import math
import random
import sys
import time

from omega_transform.harness import Candidate, CandidateError

SEED = 777
N_TRAIN = 1000
N_TEST = 1000
MAX_FEATURES = 30
RIDGE = 1.0  # added to the diagonal of X^T X (bias excluded)
CPU_LIMIT = 5.0  # CPU seconds for computing the features of all rows


def make_data(seed, n):
    rng = random.Random(seed)
    rows, labels = [], []
    for _ in range(n):
        x1, x2, x6 = rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)
        x3 = math.exp(rng.gauss(0, 1))
        x4, x5 = rng.uniform(-2, 2), rng.uniform(-2, 2)
        z = (1.4 * (1 if x1 * x2 > 0 else -1)
             + 1.2 * (1 if x4 * x4 + x5 * x5 < 2.6 else -1)
             + 1.0 * (1 if math.log(x3) > 0.0 else -1)
             + rng.gauss(0, 0.6))
        rows.append([x1, x2, x3, x4, x5, x6])
        labels.append(1 if z > 0 else 0)
    return rows, labels


def solve(a, b):
    """Solve a x = b (a is k x k, symmetric positive definite here) by Gaussian elimination."""
    k = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        for r in range(col + 1, k):
            f = m[r][col] / p
            if f:
                mr, mc = m[r], m[col]
                for c in range(col, k + 1):
                    mr[c] -= f * mc[c]
    x = [0.0] * k
    for r in range(k - 1, -1, -1):
        x[r] = (m[r][k] - sum(m[r][c] * x[c] for c in range(r + 1, k))) / m[r][r]
    return x


def train_and_score(feats, labels):
    """Fixed model. Returns (train accuracy, test accuracy, weights, per test row:
    classified correctly)."""
    tr, te = feats[:N_TRAIN], feats[N_TRAIN:]
    d = len(tr[0])
    mean = [sum(r[j] for r in tr) / N_TRAIN for j in range(d)]
    std = [math.sqrt(sum((r[j] - mean[j]) ** 2 for r in tr) / N_TRAIN) for j in range(d)]
    std = [s if s > 1e-12 else 1.0 for s in std]

    def design(r):
        return [1.0] + [(r[j] - mean[j]) / std[j] for j in range(d)]

    xtr = [design(r) for r in tr]
    ytr = [1.0 if y else -1.0 for y in labels[:N_TRAIN]]
    k = d + 1
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for x, y in zip(xtr, ytr):
        for i in range(k):
            xi = x[i]
            xty[i] += xi * y
            row = xtx[i]
            for j in range(i, k):
                row[j] += xi * x[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
        if i:
            xtx[i][i] += RIDGE
    w = solve(xtx, xty)

    def hits(rows, ys):
        return [(sum(a * b for a, b in zip(w, design(r))) > 0) == bool(y)
                for r, y in zip(rows, ys)]

    train_hits, test_hits = hits(tr, labels[:N_TRAIN]), hits(te, labels[N_TRAIN:])
    return sum(train_hits) / N_TRAIN, sum(test_hits) / N_TEST, w, test_hits


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def main(path):
    rows, labels = make_data(SEED, N_TRAIN + N_TEST)
    cand = Candidate(path)
    secs = 0.0
    feats = []
    for i, row in enumerate(rows):
        try:
            f = cand.call("features", list(row))
        except CandidateError as e:
            fail(str(e))
        secs += cand.last_cpu
        try:
            f = [float(v) for v in f]
        except (TypeError, ValueError):
            fail(f"features() for row {i} must return a list of numbers, got {f!r:.120}")
        if not 1 <= len(f) <= MAX_FEATURES:
            fail(f"features() returned {len(f)} values for row {i}; allowed are 1 to "
                 f"{MAX_FEATURES}")
        if feats and len(f) != len(feats[0]):
            fail(f"features() returned {len(f)} values for row {i} but {len(feats[0])} "
                 "for row 0; every row needs the same number of features")
        bad = [j for j, v in enumerate(f) if not math.isfinite(v)]
        if bad:
            fail(f"feature {bad[0]} of row {i} is {f[bad[0]]} for input "
                 f"{[round(v, 4) for v in row]}; all features must be finite")
        feats.append(f)
    if secs > CPU_LIMIT:
        fail(f"too slow: computing the features took {secs:.2f} CPU seconds "
             f"(limit {CPU_LIMIT:.0f})")
    t1 = time.process_time()
    train_acc, test_acc, w, test_hits = train_and_score(feats, labels)
    train_s = time.process_time() - t1
    majority = max(sum(labels[N_TRAIN:]), N_TEST - sum(labels[N_TRAIN:])) / N_TEST
    top = sorted(range(1, len(w)), key=lambda j: -abs(w[j]))[:8]
    feedback = (f"test accuracy {test_acc:.3f} (train {train_acc:.3f}) with {len(w) - 1} "
                f"features; majority-class baseline {majority:.3f}. Largest standardized "
                "weights: " + ", ".join(f"f{j - 1}={w[j]:+.3f}" for j in top))
    print(json.dumps({
        "score": test_acc,
        "metrics": {"test_accuracy": test_acc, "train_accuracy": train_acc,
                    "num_features": len(w) - 1, "majority_baseline": majority,
                    "feature_cpu_s": secs, "train_cpu_s": train_s},
        "features": {"num_features": len(w) - 1},
        "solution": {"kind": "classification", "columns": ["x1", "x2", "x3", "x4", "x5", "x6"],
                     "head": [[round(v, 2) for v in r] for r in rows[:4]],
                     "head_labels": labels[:4], "train_rows": N_TRAIN,
                     "num_features": len(w) - 1,
                     "test_correct": "".join("1" if h else "0" for h in test_hits)},
        "valid": True,
        "feedback": feedback,
    }))


if __name__ == "__main__":
    main(sys.argv[1])

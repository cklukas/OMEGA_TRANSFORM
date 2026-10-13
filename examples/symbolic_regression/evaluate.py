# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for symbolic regression: score = R^2 of the predictions on held-out data.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)

The data come from a hidden formula plus Gaussian noise (sigma 0.1). The candidate
fits on 300 training rows. Score = mean of the test R^2 inside the training range and
the R^2 on an extrapolation set with wider input ranges, each clipped to [0, 1]. The
extrapolation half rewards the true functional form over a flexible curve fit.
"""
import json
import math
import random
import sys

from omega_transform.harness import Candidate, CandidateError

SEED = 4242
NOISE = 0.1
CPU_LIMIT = 10.0
TRAIN_RANGE = ((-3.0, 3.0), (-2.0, 2.0), (0.0, 3.0))
EXTRA_RANGE = ((-4.5, 4.5), (-3.0, 3.0), (0.0, 4.0))


def hidden(x1, x2, x3):
    return 2.5 * math.sin(x1) * x2 + 0.5 * x3 * x3 - 1.3


def sample(rng, n, ranges):
    rows = []
    for _ in range(n):
        x = [rng.uniform(lo, hi) for lo, hi in ranges]
        rows.append((*x, hidden(*x) + rng.gauss(0.0, NOISE)))
    return rows


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def r2(ys, ps):
    mean = sum(ys) / len(ys)
    sst = sum((y - mean) ** 2 for y in ys)
    sse = sum((y - p) ** 2 for y, p in zip(ys, ps))
    return 1.0 - sse / sst, math.sqrt(sse / len(ys))


def main(path):
    rng = random.Random(SEED)
    train = sample(rng, 300, TRAIN_RANGE)
    test = sample(rng, 300, TRAIN_RANGE)
    extra = sample(rng, 300, EXTRA_RANGE)
    cand = Candidate(path)
    try:
        params = cand.call("fit", [list(r) for r in train])
    except CandidateError as e:
        msg = str(e)
        if e.message.startswith("TypeError: cannot return"):
            msg = ("fit() must return plain data (numbers, strings, lists, dicts) as params: "
                   + msg)
        fail(msg)
    secs = cand.last_cpu
    preds = {}
    for name, rows in (("test", test), ("extrapolation", extra)):
        out = []
        for x1, x2, x3, _ in rows:
            try:
                p = cand.call("predict", params, x1, x2, x3)
            except CandidateError as e:
                fail(str(e))
            secs += cand.last_cpu
            try:
                p = float(p)
            except (TypeError, ValueError):
                fail(f"predict{(round(x1, 4), round(x2, 4), round(x3, 4))} returned "
                     f"{type(p).__name__} {p!r:.80}, not a number")
            if not math.isfinite(p):
                fail(f"predict{(round(x1, 4), round(x2, 4), round(x3, 4))} returned {p} "
                     f"on the {name} set; every prediction must be a finite float")
            out.append(p)
        preds[name] = out
    if secs > CPU_LIMIT:
        fail(f"too slow: fit and predictions took {secs:.2f} CPU seconds (limit {CPU_LIMIT:.0f})")
    r2_test, rmse_test = r2([r[3] for r in test], preds["test"])
    r2_extra, rmse_extra = r2([r[3] for r in extra], preds["extrapolation"])
    score = 0.5 * (min(1.0, max(0.0, r2_test)) + min(1.0, max(0.0, r2_extra)))
    worst = sorted(zip(extra, preds["extrapolation"]), key=lambda t: -abs(t[0][3] - t[1]))[:5]
    feedback = (f"test R^2 {r2_test:.4f} (RMSE {rmse_test:.4f}), extrapolation R^2 "
                f"{r2_extra:.4f} (RMSE {rmse_extra:.4f}); noise sigma is {NOISE}. "
                "Largest extrapolation errors (x1, x2, x3 -> y true / predicted): " +
                "; ".join(f"({r[0]:.2f}, {r[1]:.2f}, {r[2]:.2f}) -> {r[3]:.3f} / {p:.3f}"
                          for r, p in worst))
    print(json.dumps({
        "score": score,
        "metrics": {"r2_test": r2_test, "rmse_test": rmse_test, "r2_extrapolation": r2_extra,
                    "rmse_extrapolation": rmse_extra, "noise_sigma": NOISE, "cpu_s": secs},
        "features": {"rmse_test": rmse_test},
        "valid": True,
        "feedback": feedback,
    }))


if __name__ == "__main__":
    main(sys.argv[1])

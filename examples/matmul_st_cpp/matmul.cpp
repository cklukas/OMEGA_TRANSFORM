// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// Single-threaded, single-precision square matrix multiply C = A * B
// (row-major, n x n), scored by CPU time rather than wall-clock time, so the
// score stays comparable while other programs run on the machine.
//
// This file is compiled on its own and linked with harness.cpp, which times
// matmul with the process CPU clock and checks the result against a
// double-precision reference. Extra threads do not help: their CPU time adds up.
// Only the OMEGA-BLOCK region evolves; it may define only matmul as a global
// symbol (helpers must be static or in an anonymous namespace).
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <vector>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

// OMEGA-BLOCK-START
// Computes C = A * B on the calling thread. C is NOT zeroed by the caller.
// n is any size from 1 to 2048.
extern "C" void matmul(int n, const float* A, const float* B, float* C) {
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < n; ++j) {
            float s = 0.0f;
            for (int k = 0; k < n; ++k) s += A[i * n + k] * B[k * n + j];
            C[i * n + j] = s;
        }
}
// OMEGA-BLOCK-END

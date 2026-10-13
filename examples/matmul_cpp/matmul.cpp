// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// Single-precision square matrix multiply C = A * B (row-major, n x n).
//
// This file is compiled on its own and linked with harness.cpp, which times
// matmul (wall-clock) and checks the result against a double-precision
// reference. Only the OMEGA-BLOCK region evolves; it may define only matmul as
// a global symbol (helpers must be static or in an anonymous namespace).
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

// OMEGA-BLOCK-START
// Computes C = A * B. C is NOT zeroed by the caller. Any number of threads may be
// used (std::thread is available); n is any size from 1 to 2048.
extern "C" void matmul(int n, const float* A, const float* B, float* C) {
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < n; ++j) {
            float s = 0.0f;
            for (int k = 0; k < n; ++k) s += A[i * n + k] * B[k * n + j];
            C[i * n + j] = s;
        }
}
// OMEGA-BLOCK-END

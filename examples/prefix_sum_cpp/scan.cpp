// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// Single-threaded inclusive prefix sum of int64 values fused with a 256-bin
// histogram of their low bytes, scored by CPU time rather than wall-clock
// time, so the score stays comparable while other programs run on the machine.
//
// This file is compiled on its own and linked with harness.cpp, which times
// scan_and_histogram with the process CPU clock and checks both results exactly
// against a simple reference loop. Extra threads do not help: their CPU time
// adds up. Only the OMEGA-BLOCK region evolves; it may define only
// scan_and_histogram as a global symbol (helpers must be static or in an
// anonymous namespace).
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <vector>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

// OMEGA-BLOCK-START
// Computes, on the calling thread, out[i] = in[0] + ... + in[i] for i < n, and
// hist[b] = number of i < n with (in[i] & 255) == b for b < 256. Neither out nor
// hist is zeroed by the caller. n is any size from 0 to 20'000'000; the sums
// never overflow int64.
extern "C" void scan_and_histogram(const int64_t* in, int64_t* out, size_t n, uint64_t* hist) {
    for (int b = 0; b < 256; ++b) hist[b] = 0;
    int64_t sum = 0;
    for (size_t i = 0; i < n; ++i) {
        sum += in[i];
        out[i] = sum;
        hist[in[i] & 255]++;
    }
}
// OMEGA-BLOCK-END

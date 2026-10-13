// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// Single-threaded in-place sort of 32-bit unsigned integers, scored by CPU time.
//
// This file is compiled on its own and linked with harness.cpp, which generates
// the inputs, times sort_u32 with the process CPU clock and checks every result
// against std::sort. Only the OMEGA-BLOCK region evolves; it may define only
// sort_u32 as a global symbol (helpers must be static or in an anonymous namespace).
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
// Sorts a[0..n) ascending, in place, on the calling thread. n is any size from
// 0 to 2'000'000. Extra memory may be allocated freely.
static void merge_sort_rec(uint32_t* a, uint32_t* tmp, size_t lo, size_t hi) {
    if (hi - lo < 2) return;
    size_t mid = lo + (hi - lo) / 2;
    merge_sort_rec(a, tmp, lo, mid);
    merge_sort_rec(a, tmp, mid, hi);
    size_t i = lo, j = mid, k = lo;
    while (i < mid && j < hi) tmp[k++] = (a[j] < a[i]) ? a[j++] : a[i++];
    while (i < mid) tmp[k++] = a[i++];
    while (j < hi) tmp[k++] = a[j++];
    for (size_t t = lo; t < hi; ++t) a[t] = tmp[t];
}

extern "C" void sort_u32(uint32_t* a, size_t n) {
    std::vector<uint32_t> tmp(n);
    merge_sort_rec(a, tmp.data(), 0, n);
}
// OMEGA-BLOCK-END

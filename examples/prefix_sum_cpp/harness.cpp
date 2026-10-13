// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// The fixed harness of the prefix_sum_cpp example, compiled separately from the
// candidate (scan.cpp) and linked with it: the candidate cannot change it, and
// its macros and helpers cannot reach the candidate or the other way round.
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

// The candidate's interface (defined in scan.cpp).
extern "C" void scan_and_histogram(const int64_t* in, int64_t* out, size_t n, uint64_t* hist);

static double cpu_seconds() {
    timespec ts;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(int argc, char** argv) {
    size_t n = argc > 1 ? (size_t)std::atoll(argv[1]) : 10000000;
    int reps = argc > 2 ? std::atoi(argv[2]) : 10;
    std::vector<int64_t> in(n), out(n + 1), ref(n);
    std::vector<uint64_t> hist(257), ref_hist(256, 0);
    // Values in [-2^31, 2^31): no partial sum of 20M of them can overflow. Half of
    // them are multiples of 256 (low byte 0), runs of others share one low byte, so
    // the histogram has hot bins as real data often does.
    uint64_t s = 0x853C49E6748FEA9Bull;
    auto rnd = [&] {
        s = s * 6364136223846793005ull + 1442695040888963407ull;
        return s >> 16;
    };
    uint64_t run_byte = 0;
    for (size_t i = 0; i < n; ++i) {
        uint64_t r = rnd();
        int64_t v = (int64_t)(r & 0xFFFFFFFFull) - 2147483648ll;
        int kind = (int)((r >> 32) & 7);
        if (kind < 4) v &= ~(int64_t)255;
        else if (kind < 6) {
            if (((r >> 36) & 63) == 0) run_byte = rnd() & 255;
            v = (v & ~(int64_t)255) | (int64_t)run_byte;
        }
        in[i] = v;
    }
    int64_t sum = 0;
    for (size_t i = 0; i < n; ++i) {
        sum += in[i];
        ref[i] = sum;
        ref_hist[in[i] & 255]++;
    }

    const int64_t CANARY = 0x5A5A5A5A5A5A5A5All;
    double best = 1e30;
    for (int r = 0; r < reps; ++r) {
        // garbage: scan_and_histogram must write every output and every bin
        for (size_t i = 0; i < n; ++i) out[i] = (int64_t)0x7EADBEEF00000000ll + (int64_t)i * 977;
        out[n] = CANARY;
        for (int b = 0; b < 256; ++b) hist[b] = 1000003u * (b + 1);
        hist[256] = (uint64_t)CANARY;
        double t0 = cpu_seconds();
        scan_and_histogram(in.data(), out.data(), n, hist.data());
        best = std::min(best, cpu_seconds() - t0);
        if (out[n] != CANARY || hist[256] != (uint64_t)CANARY) {
            std::printf("{\"n\": %zu, \"ok\": false, \"what\": \"wrote past the end of %s\"}\n",
                        n, out[n] != CANARY ? "out" : "hist");
            return 0;
        }
        for (size_t i = 0; i < n; ++i)
            if (out[i] != ref[i]) {
                std::printf("{\"n\": %zu, \"ok\": false, \"what\": \"out[%zu] = %lld, expected %lld "
                            "(in[%zu] = %lld)\"}\n", n, i, (long long)out[i], (long long)ref[i], i,
                            (long long)in[i]);
                return 0;
            }
        for (int b = 0; b < 256; ++b)
            if (hist[b] != ref_hist[b]) {
                std::printf("{\"n\": %zu, \"ok\": false, \"what\": \"hist[%d] = %llu, expected %llu\"}\n",
                            n, b, (unsigned long long)hist[b], (unsigned long long)ref_hist[b]);
                return 0;
            }
    }
    best = std::max(best, 1e-9);  // tiny n can finish below the clock's resolution
    double gbps = (double)n * sizeof(int64_t) / best / 1e9;
    std::printf("{\"n\": %zu, \"ok\": true, \"cpu_seconds\": %.6g, \"gb_per_s\": %.6g, "
                "\"ns_per_element\": %.6g}\n",
                n, best, gbps, n ? best * 1e9 / (double)n : 0.0);
    return 0;
}

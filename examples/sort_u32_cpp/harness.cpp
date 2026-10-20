// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// The fixed harness of the sort_u32_cpp example, compiled separately from the
// candidate (sort.cpp) and linked with it: the candidate cannot change it, and
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
#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <intrin.h>
#endif

// The candidate's interface (defined in sort.cpp).
extern "C" void sort_u32(uint32_t* a, size_t n);

#if defined(_WIN32)
// Windows advances a process's CPU times only once per scheduler tick (15.6 ms), too
// coarse for these timings. The CPU cycles its threads ran are exact, and with an
// invariant TSC they count at its constant rate, measured here against the
// performance counter.
static double tsc_hz() {
    static double hz = 0;
    if (hz == 0) {
        LARGE_INTEGER f, q0, q1;
        QueryPerformanceFrequency(&f);
        QueryPerformanceCounter(&q0);
        unsigned long long c0 = __rdtsc();
        Sleep(20);
        QueryPerformanceCounter(&q1);
        hz = (__rdtsc() - c0) / (double(q1.QuadPart - q0.QuadPart) / f.QuadPart);
    }
    return hz;
}
#endif

static double cpu_seconds() {
#if defined(_WIN32)
    ULONG64 cycles = 0;
    QueryProcessCycleTime(GetCurrentProcess(), &cycles);
    return cycles / tsc_hz();
#else
    timespec ts;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
#endif
}

static const char* const DIST_NAMES[] = {"uniform", "dups1000", "sorted", "reverse", "nearly_sorted"};
static const int NUM_DISTS = 5;

// Deterministic input of distribution d; every repetition gets its own seed,
// so a candidate cannot reuse a result from an earlier call.
static void generate(int d, uint32_t seed, uint32_t* a, size_t n) {
    uint64_t s = 0x9E3779B97F4A7C15ull ^ ((uint64_t)seed * 2654435761u + (uint64_t)d * 40503u);
    auto rnd = [&] {
        s = s * 6364136223846793005ull + 1442695040888963407ull;
        return (uint32_t)(s >> 32);
    };
    for (size_t i = 0; i < n; ++i) a[i] = rnd();
    if (d == 1) {
        for (size_t i = 0; i < n; ++i) a[i] %= 1000u;
    } else if (d == 2 || d == 4) {
        std::sort(a, a + n);
    } else if (d == 3) {
        std::sort(a, a + n, [](uint32_t x, uint32_t y) { return x > y; });
    }
    if (d == 4 && n > 1) {  // sorted, then about 1% of the elements swapped at random
        for (size_t k = 0; k < n / 100 + 1; ++k) std::swap(a[rnd() % n], a[rnd() % n]);
    }
}

int main(int argc, char** argv) {
    size_t n = argc > 1 ? (size_t)std::atoll(argv[1]) : 1000000;
    int reps = argc > 2 ? std::atoi(argv[2]) : 3;
    std::vector<uint32_t> a(n + 1), ref(n);
    double log_sum = 0.0;
    bool any_zero = false;
    char per_dist[1024];
    size_t pos = 0;
    per_dist[0] = '\0';
    for (int d = 0; d < NUM_DISTS; ++d) {
        double best = 1e30;
        for (int r = 0; r < reps; ++r) {
            generate(d, 1000u + (uint32_t)r, a.data(), n);
            a[n] = 0xA5A5A5A5u;  // canary just past the end
            std::memcpy(ref.data(), a.data(), n * sizeof(uint32_t));
            double t0 = cpu_seconds();
            sort_u32(a.data(), n);
            best = std::min(best, cpu_seconds() - t0);
            std::sort(ref.begin(), ref.end());
            if (a[n] != 0xA5A5A5A5u) {
                std::printf("{\"n\": %zu, \"ok\": false, \"dist\": \"%s\", \"index\": %zu, "
                            "\"got\": %u, \"expected\": %u, \"note\": \"wrote past the end\"}\n",
                            n, DIST_NAMES[d], n, a[n], 0xA5A5A5A5u);
                return 0;
            }
            for (size_t i = 0; i < n; ++i)
                if (a[i] != ref[i]) {
                    std::printf("{\"n\": %zu, \"ok\": false, \"dist\": \"%s\", \"index\": %zu, "
                                "\"got\": %u, \"expected\": %u}\n",
                                n, DIST_NAMES[d], i, a[i], ref[i]);
                    return 0;
                }
        }
        best = std::max(best, 1e-9);  // tiny n can finish below the clock's resolution
        double meps = (double)n / best / 1e6;
        if (meps > 0.0) log_sum += std::log(meps); else any_zero = true;
        pos += std::snprintf(per_dist + pos, sizeof(per_dist) - pos,
                             "%s\"%s\": {\"cpu_seconds\": %.6g, \"melem_per_s\": %.6g}",
                             d ? ", " : "", DIST_NAMES[d], best, meps);
    }
    double geo = any_zero ? 0.0 : std::exp(log_sum / NUM_DISTS);
    if (!std::isfinite(geo)) geo = 0.0;  // keep the JSON valid
    std::printf("{\"n\": %zu, \"ok\": true, \"geomean_melem_per_s\": %.6g, \"dists\": {%s}}\n",
                n, geo, per_dist);
    return 0;
}

// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// The fixed harness of the matmul_st_cpp example, compiled separately from the
// candidate (matmul.cpp) and linked with it: the candidate cannot change it, and
// its macros and helpers cannot reach the candidate or the other way round.
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

// The candidate's interface (defined in matmul.cpp).
extern "C" void matmul(int n, const float* A, const float* B, float* C);

static double cpu_seconds() {
    timespec ts;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static void reference(int n, const float* A, const float* B, double* C) {
    std::fill(C, C + (size_t)n * n, 0.0);
    for (int i = 0; i < n; ++i)
        for (int k = 0; k < n; ++k) {
            double a = A[(size_t)i * n + k];
            for (int j = 0; j < n; ++j) C[(size_t)i * n + j] += a * B[(size_t)k * n + j];
        }
}

int main(int argc, char** argv) {
    int n = argc > 1 ? std::atoi(argv[1]) : 512;
    int reps = argc > 2 ? std::atoi(argv[2]) : 3;
    size_t nn = (size_t)n * n;
    std::vector<float> A(nn), B(nn), C(nn);
    std::vector<double> R(nn);
    unsigned s = 12345u;
    auto rnd = [&] { s = s * 1664525u + 1013904223u; return (float)((s >> 8) & 0xFFFF) / 65536.0f - 0.5f; };
    for (auto& v : A) v = rnd();
    for (auto& v : B) v = rnd();

    double best = 1e30;
    for (int r = 0; r < reps; ++r) {
        std::fill(C.begin(), C.end(), 12345.0f);  // garbage: matmul must overwrite C
        double t0 = cpu_seconds();
        matmul(n, A.data(), B.data(), C.data());
        best = std::min(best, cpu_seconds() - t0);
    }
    reference(n, A.data(), B.data(), R.data());
    double max_err = 0.0;
    for (size_t i = 0; i < nn; ++i) {
        double e = std::fabs((double)C[i] - R[i]);
        if (!(e <= max_err)) max_err = std::isnan(e) ? INFINITY : std::max(max_err, e);
    }
    best = std::max(best, 1e-9);  // tiny n can finish below the clock's resolution
    double gflops = 2.0 * n * (double)n * n / best / 1e9;
    if (!std::isfinite(max_err)) max_err = 1e30;  // keep the JSON valid
    std::printf("{\"n\": %d, \"cpu_seconds\": %.6g, \"gflops\": %.6g, \"max_err\": %.6g}\n",
                n, best, gflops, max_err);
    return 0;
}

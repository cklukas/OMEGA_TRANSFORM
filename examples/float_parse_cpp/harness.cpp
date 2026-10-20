// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// The fixed harness of the float_parse_cpp example, compiled separately from the
// candidate (parse.cpp) and linked with it: the candidate cannot change it, and
// its macros and helpers cannot reach the candidate or the other way round.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
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

// The candidate's interface (defined in parse.cpp).
extern "C" size_t parse_doubles(const char* buf, size_t len, double* out);

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

// Hard cases first: they are part of every text, also in the benchmark.
static const char* const EDGE[] = {
    "0", "-0", "1", "-1.5", "0.1", "1e23", "9007199254740993", "4.9e-324", "5e-324",
    "2.4703282292062327e-324", "2.4703282292062328e-324", "2.2250738585072011e-308",
    "2.2250738585072014e-308", "1.7976931348623157e308", "1.7976931348623158E+308",
    "0.30000000000000004", "123456789012345678901234567890", "000123.4500", "1E5",
    "1.00000000000000011102230246251565404236316680908203125",
    "1.00000000000000011102230246251565404236316680908203126",
    "7.2057594037927933e16", "-8.98846567431158e307", "1e-5", "18446744073709551615",
    "18446744073709551616", "3.141592653589793238462643383279502884197", "1e+00",
    "0.000000000000000000000000000000000000000001", "-12345678901234567890.123456789e-10",
};
static const size_t NUM_EDGE = sizeof(EDGE) / sizeof(EDGE[0]);

static std::string generate(size_t count) {
    std::string text;
    text.reserve(count * 24 + 64);
    uint64_t s = 0x2545F4914F6CDD1Dull;
    auto rnd = [&] {
        s = s * 6364136223846793005ull + 1442695040888963407ull;
        return s >> 11;  // 53 random bits
    };
    char tmp[128];
    for (size_t i = 0; i < count; ++i) {
        if (i < NUM_EDGE) {
            text += EDGE[i];
            text += '\n';
            continue;
        }
        uint64_t r = rnd();
        int kind = (int)(r % 10);
        bool neg = (r >> 4) & 1;
        double m = (double)(rnd()) / 9007199254740992.0;  // [0, 1)
        int len = 0;
        switch (kind) {
        case 0:  // integer with 1 to 15 digits
            len = std::snprintf(tmp, sizeof tmp, "%s%llu", neg ? "-" : "",
                                (unsigned long long)(rnd() % (uint64_t)std::pow(10.0, 1 + (int)((r >> 8) % 15))));
            break;
        case 1:  // small non-negative integer
            len = std::snprintf(tmp, sizeof tmp, "%llu", (unsigned long long)(rnd() % 1000));
            break;
        case 2:  // fixed decimal, 1 to 6 decimals
            len = std::snprintf(tmp, sizeof tmp, "%.*f", 1 + (int)((r >> 8) % 6), (neg ? -1e6 : 1e6) * m);
            break;
        case 3:  // price-like
            len = std::snprintf(tmp, sizeof tmp, "%.2f", 10000.0 * m);
            break;
        case 4:  // shortest round-trip-safe general format, moderate exponents
            len = std::snprintf(tmp, sizeof tmp, "%.17g", (neg ? -1.0 : 1.0) * m * std::pow(10.0, (int)((r >> 8) % 61) - 30));
            break;
        case 5:  // general format with 15 significant digits
            len = std::snprintf(tmp, sizeof tmp, "%.15g", (neg ? -1.0 : 1.0) * m * std::pow(10.0, (int)((r >> 8) % 41) - 20));
            break;
        case 6:  // scientific, 0 to 16 decimals, wide exponent range
        case 7:
            len = std::snprintf(tmp, sizeof tmp, "%.*e", (int)((r >> 8) % 17),
                                (neg ? -1.0 : 1.0) * (1.0 + 9.0 * m) * std::pow(10.0, (int)((r >> 16) % 601) - 300));
            break;
        case 8:  // short scientific like 1.5e-7
            len = std::snprintf(tmp, sizeof tmp, "%s%d.%de%d", neg ? "-" : "", 1 + (int)((r >> 8) % 9),
                                (int)((r >> 12) % 10), (int)((r >> 16) % 41) - 20);
            break;
        default: {  // long mantissa (19 to 24 digits) with a decimal point and exponent
            int digits = 19 + (int)((r >> 8) % 6), dot = 1 + (int)((r >> 16) % (uint64_t)(digits - 1));
            if (neg) tmp[len++] = '-';
            for (int d = 0; d < digits; ++d) {
                if (d == dot) tmp[len++] = '.';
                tmp[len++] = (char)('0' + (d == 0 ? 1 + rnd() % 9 : rnd() % 10));
            }
            len += std::snprintf(tmp + len, sizeof tmp - len, "e%d", (int)((r >> 24) % 81) - 40);
        }
        }
        text.append(tmp, (size_t)len);
        text += '\n';
    }
    return text;
}

int main(int argc, char** argv) {
    size_t count = argc > 1 ? (size_t)std::atoll(argv[1]) : 2000000;
    int reps = argc > 2 ? std::atoi(argv[2]) : 5;
    std::string text = generate(count);  // text.c_str()[len] == '\0'
    size_t len = text.size();

    std::vector<double> ref(count);
    {
        const char* p = text.c_str();
        for (size_t i = 0; i < count; ++i) {
            char* next;
            ref[i] = std::strtod(p, &next);
            p = next + 1;
        }
    }
    const uint64_t GARBAGE = 0x7FF8DEADBEEF1234ull, CANARY = 0x7FF0C0FFEE00C0DEull;
    std::vector<double> out(count + 1);
    double best = 1e30;
    for (int r = 0; r < reps; ++r) {
        for (size_t i = 0; i <= count; ++i)  // garbage: parse_doubles must write every value
            std::memcpy(&out[i], i < count ? &GARBAGE : &CANARY, 8);
        double t0 = cpu_seconds();
        size_t got = parse_doubles(text.c_str(), len, out.data());
        best = std::min(best, cpu_seconds() - t0);
        if (got != count) {
            std::printf("{\"count\": %zu, \"ok\": false, \"what\": \"returned %zu numbers, expected %zu\"}\n",
                        count, got, count);
            return 0;
        }
        uint64_t c;
        std::memcpy(&c, &out[count], 8);
        if (c != CANARY) {
            std::printf("{\"count\": %zu, \"ok\": false, \"what\": \"wrote past out[count-1]\"}\n", count);
            return 0;
        }
        for (size_t i = 0; i < count; ++i)
            if (std::memcmp(&out[i], &ref[i], 8) != 0) {
                size_t start = 0;  // locate the i-th line of the text for the message
                for (size_t k = 0; k < i; ++k) start = text.find('\n', start) + 1;
                std::string line = text.substr(start, std::min<size_t>(text.find('\n', start) - start, 80));
                uint64_t gb, eb;
                std::memcpy(&gb, &out[i], 8);
                std::memcpy(&eb, &ref[i], 8);
                std::printf("{\"count\": %zu, \"ok\": false, \"what\": \"value %zu (text \\\"%s\\\"): "
                            "got %.17g (bits 0x%016llx), expected %.17g (bits 0x%016llx)\"}\n",
                            count, i, line.c_str(), out[i], (unsigned long long)gb, ref[i],
                            (unsigned long long)eb);
                return 0;
            }
    }
    best = std::max(best, 1e-9);  // tiny inputs can finish below the clock's resolution
    double mbps = (double)len / best / 1e6;
    std::printf("{\"count\": %zu, \"ok\": true, \"bytes\": %zu, \"cpu_seconds\": %.6g, "
                "\"mb_per_s\": %.6g, \"ns_per_number\": %.6g}\n",
                count, len, best, mbps, count ? best * 1e9 / (double)count : 0.0);
    return 0;
}

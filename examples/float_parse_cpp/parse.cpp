// Copyright (c) 2026 by C. Klukas
// SPDX-License-Identifier: MIT
// Single-threaded parsing of newline-separated decimal numbers into doubles,
// scored by CPU time rather than wall-clock time, so the score stays comparable
// while other programs run on the machine.
//
// This file is compiled on its own and linked with harness.cpp, which generates
// a text of numbers in mixed formats, times parse_doubles with the process CPU
// clock and checks every value bit for bit against std::strtod. Extra threads do
// not help: their CPU time adds up. Only the OMEGA-BLOCK region evolves; it may
// define only parse_doubles as a global symbol (helpers must be static or in an
// anonymous namespace).
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

// OMEGA-BLOCK-START
// Parses the numbers in buf[0..len) into out[0..) and returns how many there are.
// Every number is followed by exactly one '\n'; buf[len] is '\0'. Grammar of a
// number: optional '-', one or more digits, optionally '.' and one or more digits,
// optionally 'e' or 'E', an optional '+' or '-', and one or more digits. No
// spaces, no '+' sign on the mantissa, no inf/nan/hex. Every value must be
// bit-identical to std::strtod (correctly rounded, round-half-to-even).
extern "C" size_t parse_doubles(const char* buf, size_t len, double* out) {
    const char* p = buf;
    const char* end = buf + len;
    size_t count = 0;
    while (p < end) {
        char* next;
        out[count++] = std::strtod(p, &next);
        p = next;
        while (p < end && *p == '\n') ++p;
    }
    return count;
}
// OMEGA-BLOCK-END

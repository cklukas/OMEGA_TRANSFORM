# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Lossless compressor for web-server access logs: maximize the compression ratio.

The evaluator calls compress() on a fixed ~300 KB access log (Apache "combined"
format) and checks that decompress() restores it exactly. It also round-trips a few
other inputs (empty, random bytes, repetitive text, a log made with another seed), so
both functions must work for ANY bytes.
"""
import zlib

# OMEGA-BLOCK-START
def compress(data: bytes) -> bytes:
    """Compress arbitrary bytes losslessly and return the compressed blob.

    decompress(compress(data)) must equal data exactly for EVERY input, including
    b"" and non-text bytes. The main input is an access log, one request per line:
      IP - - [DD/Mon/YYYY:HH:MM:SS +0000] "METHOD PATH HTTP/1.1" STATUS SIZE "REFERER" "USER-AGENT"
    Only the Python standard library may be used (zlib, lzma, bz2, struct, re, ...).
    Compress plus decompress of the main input must stay within 20 CPU seconds.

    Current method: plain zlib at level 1.
    """
    return zlib.compress(data, 1)


def decompress(blob: bytes) -> bytes:
    """Invert compress() exactly."""
    return zlib.decompress(blob)
# OMEGA-BLOCK-END


def roundtrip(data: bytes) -> tuple[bytes, bytes]:
    blob = compress(data)
    return blob, decompress(blob)

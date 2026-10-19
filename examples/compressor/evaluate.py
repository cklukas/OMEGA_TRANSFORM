# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: MIT
"""Evaluator for the log compressor: exact round trips, score = compression ratio.

Usage: python evaluate.py <candidate.py>    (prints one JSON line)
"""
import bz2
import json
import lzma
import random
import sys
import time
import zlib

from omega_transform.harness import Candidate, CandidateError

SEED = 20251010
CPU_LIMIT = 20.0  # CPU seconds for compress + decompress of the main dataset

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (compatible; ExampleBot/2.1; +http://bot.example.org/info)",
    "curl/8.5.0",
    "python-requests/2.31.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.80",
]
AGENT_W = [30, 14, 6, 16, 12, 5, 7, 3, 3, 4]
CATEGORIES = ["books", "music", "garden", "kitchen", "toys", "sports", "tools", "office"]
STATIC = ["/static/css/main.css", "/static/js/app.js", "/static/js/vendor.js",
          "/static/img/logo.png", "/favicon.ico", "/static/fonts/inter.woff2"]
STATIC_SIZE = [18342, 120554, 389211, 7421, 1150, 48230]
REFERERS = ["-", "https://www.example.com/", "https://www.example.com/search",
            "https://search.example.net/?q=shop", "https://social.example.org/feed"]


def make_log(seed, lines):
    """A deterministic access log with realistic skew and repetition."""
    rng = random.Random(seed)
    ips = [f"{rng.choice([10, 172, 192, 198, 203])}.{rng.randrange(256)}."
           f"{rng.randrange(256)}.{rng.randrange(1, 255)}" for _ in range(400)]
    ip_w = [1.0 / (k + 1) ** 1.1 for k in range(len(ips))]  # Zipf-like popularity
    t = 1_728_000_000 + rng.randrange(86400)
    out = []
    session = None
    for _ in range(lines):
        t += int(rng.expovariate(1 / 2.2))
        if session is None or rng.random() < 0.3:
            ip = rng.choices(ips, ip_w)[0]
            agent = rng.choices(AGENTS, AGENT_W)[0]
            session = (ip, agent)
        ip, agent = session
        tm = time.gmtime(t)
        stamp = (f"{tm.tm_mday:02d}/{MONTHS[tm.tm_mon - 1]}/{tm.tm_year}:"
                 f"{tm.tm_hour:02d}:{tm.tm_min:02d}:{tm.tm_sec:02d} +0000")
        r = rng.random()
        method = "GET"
        referer = rng.choices(REFERERS, [40, 30, 15, 10, 5])[0]
        if r < 0.35:
            k = rng.randrange(len(STATIC))
            path, size = STATIC[k], STATIC_SIZE[k]
            status = rng.choices([200, 304], [55, 45])[0]
            referer = "https://www.example.com/"
        elif r < 0.65:
            cat = rng.choices(CATEGORIES, [8, 6, 3, 4, 5, 3, 2, 1])[0]
            pid = int(rng.paretovariate(1.2) * 100) % 50000
            path = f"/products/{cat}/{pid}"
            if rng.random() < 0.2:
                path += f"?ref={rng.choice(['home', 'search', 'mail', 'ad'])}"
            size = rng.randrange(9000, 26000)
            status = rng.choices([200, 404, 500], [93, 6, 1])[0]
        elif r < 0.8:
            q = rng.choice(["lamp", "chair", "drill", "novel", "guitar", "pan", "ball", "desk"])
            path = f"/search?q={q}&page={rng.choices([1, 2, 3, 4], [70, 18, 8, 4])[0]}"
            size = rng.randrange(4000, 15000)
            status = 200
        elif r < 0.9:
            method = rng.choices(["GET", "POST"], [60, 40])[0]
            path = f"/api/v1/cart/{rng.randrange(100000, 999999)}"
            size = rng.randrange(80, 900)
            status = rng.choices([200, 201, 400, 401], [70, 15, 10, 5])[0]
        else:
            path = rng.choice(["/", "/about", "/contact", "/login", "/robots.txt", "/sitemap.xml"])
            size = rng.randrange(2000, 9000)
            status = rng.choices([200, 301, 304], [80, 10, 10])[0]
        if status in (304, 301):
            size = 0
        size_s = "-" if size == 0 else str(size)
        out.append(f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} {size_s} '
                   f'"{referer}" "{agent}"\n')
    return "".join(out).encode()


def fail(msg):
    print(json.dumps({"valid": False, "feedback": msg}))
    sys.exit(0)


def check_roundtrip(cand, name, data):
    """Return (blob, compress_cpu_s, decompress_cpu_s) or fail with a concrete reason."""
    try:
        blob = cand.call("compress", data)
        c_s = cand.last_cpu
        if not isinstance(blob, (bytes, bytearray)):
            fail(f"compress() on the {name} input returned {type(blob).__name__}, not bytes")
        back = cand.call("decompress", bytes(blob))
        d_s = cand.last_cpu
    except CandidateError as e:
        fail(f"round trip of the {name} input ({len(data)} bytes) raised {e}")
    if not isinstance(back, (bytes, bytearray)):
        fail(f"decompress() on the {name} input returned {type(back).__name__}, not bytes")
    back = bytes(back)
    if back != data:
        k = next((i for i in range(min(len(back), len(data))) if back[i] != data[i]),
                 min(len(back), len(data)))
        fail(f"round trip of the {name} input is NOT lossless: original {len(data)} bytes, "
             f"restored {len(back)} bytes, first difference at byte {k}: "
             f"expected {data[k:k + 40]!r}, got {back[k:k + 40]!r}")
    return bytes(blob), c_s, d_s


def main(path):
    data = make_log(SEED, 2000)
    rng = random.Random(SEED + 1)
    edge_cases = [
        ("empty", b""),
        ("random-bytes", bytes(rng.randrange(256) for _ in range(257))),
        ("repetitive", b"abcabcabd" * 150 + b"\x00\xff\n"),
        ("other-log", make_log(SEED + 2, 150)),
    ]
    cand = Candidate(path)
    blobs = {name: check_roundtrip(cand, name, case)[0] for name, case in edge_cases}
    blob, c_s, d_s = check_roundtrip(cand, "main", data)
    if c_s + d_s > CPU_LIMIT:
        fail(f"too slow: compress {c_s:.2f} s + decompress {d_s:.2f} s CPU exceeds the "
             f"{CPU_LIMIT:.0f} s limit on the main input")
    ratio = len(data) / max(1, len(blob))
    zlib9 = len(data) / len(zlib.compress(data, 9))
    xz = len(data) / len(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))
    bz = len(data) / len(bz2.compress(data, 9))
    feedback = (f"ratio {ratio:.3f} ({len(data)} -> {len(blob)} bytes); references: "
                f"zlib -9 {zlib9:.3f}, bz2 -9 {bz:.3f}, lzma -9e {xz:.3f}")
    print(json.dumps({
        "score": ratio,
        "metrics": {"ratio": ratio, "original_bytes": len(data), "compressed_bytes": len(blob),
                    "other_log_ratio": len(edge_cases[3][1]) / max(1, len(blobs["other-log"])),
                    "zlib9_ratio": zlib9, "bz2_ratio": bz, "lzma_ratio": xz,
                    "compress_cpu_s": c_s, "decompress_cpu_s": d_s},
        "features": {"cpu_s": c_s + d_s},
        "solution": {"kind": "compression", "input": "web server access log",
                     "head": data.decode().splitlines()[:4], "lines": data.count(b"\n"),
                     "original_bytes": len(data), "compressed_bytes": len(blob),
                     "references": {"zlib -9": round(len(data) / zlib9),
                                    "bz2 -9": round(len(data) / bz),
                                    "lzma -9e": round(len(data) / xz)}},
        "valid": True,
        "feedback": feedback,
    }))


if __name__ == "__main__":
    main(sys.argv[1])

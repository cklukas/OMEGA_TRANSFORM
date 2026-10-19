# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Draw an example's solutions as SVG for the documentation.

An evaluator may report a ``solution`` next to its score: plain data in one of
the forms below, chosen by its ``kind``. ``omega docs`` draws the initial and
the best program's solution of each example side by side
(``docs/assets/examples/<name>.svg``), from the solutions recorded in the
benchmark results. Captions are computed from the data, so a picture always
matches its own numbers.

==============  ============================================================
kind            fields
==============  ============================================================
circles         ``container`` ("square": [0,1]^2, "circle": the unit circle),
                ``circles``: [[x, y, r], ...]
heilbronn       ``points``: [[x, y], ...] in the unit square
sphere_points   ``points``: [[x, y, z], ...] on the unit sphere
tour            ``points``: [[x, y], ...] in the unit square, ``tour``: indices
bins            ``capacity``, ``lower_bound``, ``bins``: [[item size, ...], ...]
cut             ``n``, ``edges``: [[u, v, w], ...], ``side``: [0/1 per node]
regression      ``sets``: {name: [[true y, predicted y], ...]}
==============  ============================================================

``tour``, ``bins`` and ``cut`` may name the drawn ``instance``. Everything is
standard library and deterministic: the same data give the same bytes, so the
committed figures can be checked like the generated pages.
"""
from __future__ import annotations

import itertools
import math
from xml.sax.saxutils import escape

W = 400           # width of one panel
PLOT = 340        # side of a panel's square plot area
TOP = 58          # space for the panel's title and caption
H = TOP + PLOT + 22
GAP = 20          # between the two panels

INK, MUTED, GRID = "#1d2033", "#5d6178", "#d5d7e3"
ACCENT, ACCENT_DARK = "#7b6cff", "#4b3fc4"
WARN, GOOD = "#e8590c", "#0f9d8a"
BG, BORDER = "#fbfbfe", "#dcdfea"


def _f(v: float) -> str:
    """A coordinate: two decimals, no negative zero."""
    s = f"{v:.2f}"
    return "0.00" if s == "-0.00" else s


def _num(v: float, digits: int = 5) -> str:
    return f"{v:.{digits}g}"


class _Frame:
    """Maps a data box onto the square plot area of a panel at x offset ``ox``."""

    def __init__(self, ox: float, x0: float, x1: float, y0: float, y1: float, pad: float = 14):
        self.ox, self.x0, self.y0 = ox, x0, y0
        self.left = ox + (W - PLOT) / 2 + pad
        self.top = TOP + pad
        self.size = PLOT - 2 * pad
        self.sx = self.size / (x1 - x0)
        self.sy = self.size / (y1 - y0)

    def x(self, v: float) -> str:
        return _f(self.left + (v - self.x0) * self.sx)

    def y(self, v: float) -> str:  # data y grows upwards
        return _f(self.top + self.size - (v - self.y0) * self.sy)

    def len(self, v: float) -> str:
        return _f(v * self.sx)


# ------------------------------------------------------------------ the kinds
def _circles(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    circles = sol["circles"]
    out = []
    if sol.get("container") == "circle":
        fr = _Frame(ox, -1, 1, -1, 1)
        out.append(f'<circle cx="{fr.x(0)}" cy="{fr.y(0)}" r="{fr.len(1)}" fill="#fff" '
                   f'stroke="{INK}" stroke-width="1.5"/>')
    else:
        fr = _Frame(ox, 0, 1, 0, 1)
        out.append(f'<rect x="{fr.x(0)}" y="{fr.y(1)}" width="{fr.len(1)}" height="{fr.len(1)}" '
                   f'fill="#fff" stroke="{INK}" stroke-width="1.5"/>')
    rmax = max(scale.get("rmax", 0), max((r for _, _, r in circles), default=0)) or 1
    for x, y, r in circles:
        t = r / rmax  # larger circles darker
        out.append(f'<circle cx="{fr.x(x)}" cy="{fr.y(y)}" r="{fr.len(r)}" '
                   f'fill="{ACCENT}" fill-opacity="{0.18 + 0.5 * t:.2f}" stroke="{ACCENT_DARK}" '
                   'stroke-width="1"/>')
    total = sum(r for _, _, r in circles)
    return out, f"sum of radii {_num(total, 6)} · {len(circles)} circles"


def _triangle_area(p, q, s) -> float:
    return 0.5 * abs((q[0] - p[0]) * (s[1] - p[1]) - (s[0] - p[0]) * (q[1] - p[1]))


def _heilbronn(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    pts = sol["points"]
    fr = _Frame(ox, 0, 1, 0, 1)
    out = [f'<rect x="{fr.x(0)}" y="{fr.y(1)}" width="{fr.len(1)}" height="{fr.len(1)}" '
           f'fill="#fff" stroke="{INK}" stroke-width="1.5"/>']
    tris = sorted((_triangle_area(pts[a], pts[b], pts[c]), (a, b, c))
                  for a, b, c in itertools.combinations(range(len(pts)), 3))
    smallest = tris[0][0] if tris else 0.0
    near = [t for s, t in tris if s <= smallest * (1 + 1e-6) + 1e-15]
    for tri in near:
        poly = " ".join(f"{fr.x(pts[k][0])},{fr.y(pts[k][1])}" for k in tri)
        out.append(f'<polygon points="{poly}" fill="{WARN}" fill-opacity="0.25" '
                   f'stroke="{WARN}" stroke-width="1.2" stroke-linejoin="round"/>')
    for x, y in pts:
        out.append(f'<circle cx="{fr.x(x)}" cy="{fr.y(y)}" r="4.5" fill="{ACCENT_DARK}"/>')
    n = len(pts)
    return out, (f"smallest triangle {_num(smallest, 4)} (×n² = {_num(smallest * n * n, 4)})"
                 f" · {len(near)} at the minimum")


def _rotate(p):
    """A fixed oblique view, so the sphere does not show only one pole."""
    x, y, z = p
    a, b = math.radians(-25), math.radians(20)
    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
    x, z = x * math.cos(b) + z * math.sin(b), -x * math.sin(b) + z * math.cos(b)
    return x, y, z


def _cross(p, q):
    return (p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0])


def _unit(p):
    n = math.sqrt(sum(c * c for c in p)) or 1.0
    return tuple(c / n for c in p)


def _angle(p, q) -> float:
    cx = p[1] * q[2] - p[2] * q[1]
    cy = p[2] * q[0] - p[0] * q[2]
    cz = p[0] * q[1] - p[1] * q[0]
    dot = p[0] * q[0] + p[1] * q[1] + p[2] * q[2]
    return math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot)


def _sphere_points(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    pts = []
    for p in sol["points"]:
        n = math.sqrt(sum(c * c for c in p)) or 1.0
        pts.append(tuple(c / n for c in p))
    a = min((_angle(p, q) for p, q in itertools.combinations(pts, 2)), default=0.0)
    cap = a / 2  # caps of this angular radius touch but never overlap
    fr = _Frame(ox, -1, 1, -1, 1)

    def cap_outline(p, front: bool) -> str:
        """The visible outline of the cap around ``p`` seen from +z (front) or -z: the
        cap's boundary circle, with its hidden part moved onto the silhouette."""
        # an orthonormal pair perpendicular to p spans the boundary circle
        helper = (1.0, 0.0, 0.0) if abs(p[0]) < 0.9 else (0.0, 1.0, 0.0)
        u = _unit(_cross(p, helper))
        v = _cross(p, u)
        pts = []
        for k in range(48):
            t = 2 * math.pi * k / 48
            q = [math.cos(cap) * p[i] + math.sin(cap) * (math.cos(t) * u[i] + math.sin(t) * v[i])
                 for i in range(3)]
            if (q[2] < 0) == front:  # hidden from this side: onto the rim
                r = math.hypot(q[0], q[1]) or 1.0
                q = [q[0] / r, q[1] / r, 0.0]
            pts.append(f"{fr.x(q[0])},{fr.y(q[1])}")
        style = (f'fill="{ACCENT}" fill-opacity="0.45" stroke="{ACCENT_DARK}" stroke-width="1"'
                 if front else
                 f'fill="none" stroke="{ACCENT}" stroke-opacity="0.5" stroke-width="0.8" '
                 'stroke-dasharray="3 2"')
        return f'<polygon points="{" ".join(pts)}" stroke-linejoin="round" {style}/>'

    view = sorted((_rotate(p) for p in pts), key=lambda p: p[2])
    out = [cap_outline(p, False) for p in view if p[2] < 0]
    out.append(f'<circle cx="{fr.x(0)}" cy="{fr.y(0)}" r="{fr.len(1)}" fill="#fff" '
               f'fill-opacity="0.82" stroke="{INK}" stroke-width="1.5"/>')
    out += [cap_outline(p, True) for p in view if p[2] >= 0]
    out += [f'<circle cx="{fr.x(x)}" cy="{fr.y(y)}" r="2" fill="{INK}"/>'
            for x, y, z in view if z >= 0]
    return out, (f"smallest angle {_num(math.degrees(a), 5)}° · {len(pts)} points "
                 "(dashed: far side)")


def _tour(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    pts, tour = sol["points"], sol["tour"]
    fr = _Frame(ox, 0, 1, 0, 1)
    out = [f'<rect x="{fr.x(0)}" y="{fr.y(1)}" width="{fr.len(1)}" height="{fr.len(1)}" '
           f'fill="#fff" stroke="{GRID}" stroke-width="1"/>']
    path = " ".join(f"{fr.x(pts[c][0])},{fr.y(pts[c][1])}" for c in tour)
    out.append(f'<polygon points="{path}" fill="none" stroke="{ACCENT_DARK}" '
               'stroke-width="1.3" stroke-linejoin="round"/>')
    out += [f'<circle cx="{fr.x(x)}" cy="{fr.y(y)}" r="1.8" fill="{INK}"/>' for x, y in pts]
    length = sum(math.dist(pts[tour[k - 1]], pts[tour[k]]) for k in range(len(tour)))
    return out, f"tour length {_num(length, 5)} · {len(pts)} cities"


def _bins(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    cap, bins = sol["capacity"], sol["bins"]
    bins = sorted(bins, key=lambda b: (-sum(b), -len(b)))  # fullest first
    slots = max(scale.get("bins", 0), len(bins), 1)
    fr = _Frame(ox, 0, slots, 0, cap * 1.08)
    out = []
    w = fr.size / slots
    gap = min(1.0, w * 0.25)
    for k, items in enumerate(bins):
        x = fr.left + k * w
        out.append(f'<rect x="{_f(x)}" y="{fr.y(cap)}" width="{_f(w - gap)}" '
                   f'height="{_f(cap * fr.sy)}" fill="#fff" '
                   f'stroke="{GRID}" stroke-width="0.5"/>')
        level = 0
        for j, s in enumerate(sorted(items, reverse=True)):
            out.append(f'<rect x="{_f(x)}" y="{fr.y(level + s)}" width="{_f(w - gap)}" '
                       f'height="{_f(s * fr.sy)}" fill="{ACCENT if j % 2 else ACCENT_DARK}"/>')
            level += s
    lb = sol.get("lower_bound")
    if lb:
        x = _f(fr.left + lb * w)
        out.append(f'<line x1="{x}" y1="{fr.y(0)}" x2="{x}" y2="{fr.y(cap * 1.06)}" '
                   f'stroke="{GOOD}" stroke-width="1.5" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{_f(fr.left + lb * w - 4)}" y="{fr.y(cap * 1.03)}" '
                   f'text-anchor="end" font-size="11" fill="{GOOD}">lower bound {lb}</text>')
    fill = sum(map(sum, bins)) / (len(bins) * cap) if bins else 0.0
    return out, f"{len(bins)} bins, filled {100 * fill:.1f}% on average"


def _cut(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    n, side, edges = sol["n"], sol["side"], sol["edges"]
    groups = [[i for i in range(n) if side[i] == s] for s in (0, 1)]
    pos = {}
    for s, members in enumerate(groups):
        # side 0 on the left half of a circle, side 1 on the right half
        for k, i in enumerate(members):
            t = (k + 0.5) / max(1, len(members))
            ang = math.pi / 2 + (t * math.pi if s == 0 else -t * math.pi)
            pos[i] = (math.cos(ang), math.sin(ang))
    fr = _Frame(ox, -1.08, 1.08, -1.08, 1.08)
    wmax = max((w for _, _, w in edges), default=1)
    out = []
    for crossing in (True, False):  # the edges that do not count are drawn on top
        for u, v, w in edges:
            if (side[u] != side[v]) != crossing:
                continue
            color, op = (GOOD, 0.10 + 0.25 * w / wmax) if crossing else \
                (WARN, 0.25 + 0.55 * w / wmax)
            out.append(f'<line x1="{fr.x(pos[u][0])}" y1="{fr.y(pos[u][1])}" '
                       f'x2="{fr.x(pos[v][0])}" y2="{fr.y(pos[v][1])}" stroke="{color}" '
                       f'stroke-opacity="{op:.2f}" stroke-width="1"/>')
    for i in range(n):
        out.append(f'<circle cx="{fr.x(pos[i][0])}" cy="{fr.y(pos[i][1])}" r="2.6" '
                   f'fill="{INK if side[i] == 0 else ACCENT}"/>')
    y = _f(TOP + PLOT + 10)
    out.append(f'<line x1="{ox + 16}" y1="{y}" x2="{ox + 34}" y2="{y}" stroke="{GOOD}" '
               f'stroke-width="2"/><text x="{ox + 40}" y="{_f(TOP + PLOT + 14)}" font-size="11" '
               f'fill="{MUTED}">cut edge (counts)</text>'
               f'<line x1="{ox + 160}" y1="{y}" x2="{ox + 178}" y2="{y}" stroke="{WARN}" '
               f'stroke-width="2"/><text x="{ox + 184}" y="{_f(TOP + PLOT + 14)}" font-size="11" '
               f'fill="{MUTED}">edge within a side (lost)</text>')
    total = sum(w for _, _, w in edges)
    cut = sum(w for u, v, w in edges if side[u] != side[v])
    return out, (f"cut {cut} of {total} edge weight ({100 * cut / max(1, total):.1f}%) · "
                 f"sides {len(groups[0])}/{len(groups[1])}")


def _r2(pairs) -> float:
    ys = [y for y, _ in pairs]
    mean = sum(ys) / len(ys)
    sst = sum((y - mean) ** 2 for y in ys) or 1.0
    return 1.0 - sum((y - p) ** 2 for y, p in pairs) / sst


SET_COLORS = [ACCENT_DARK, WARN, GOOD]


def _regression(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    sets = sol["sets"]
    lo, hi = scale.get("range") or _range(sol)
    fr = _Frame(ox, lo, hi, lo, hi, pad=22)
    out = [f'<rect x="{fr.x(lo)}" y="{fr.y(hi)}" width="{fr.len(hi - lo)}" '
           f'height="{fr.len(hi - lo)}" fill="#fff" stroke="{GRID}" stroke-width="1"/>',
           f'<line x1="{fr.x(lo)}" y1="{fr.y(lo)}" x2="{fr.x(hi)}" y2="{fr.y(hi)}" '
           f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="4 3"/>',
           f'<text x="{_f(fr.left + fr.size / 2)}" y="{_f(fr.top + fr.size + 16)}" '
           f'text-anchor="middle" font-size="11" fill="{MUTED}">true value</text>',
           f'<text x="{_f(fr.left - 8)}" y="{_f(fr.top + fr.size / 2)}" text-anchor="middle" '
           f'font-size="11" fill="{MUTED}" transform="rotate(-90 {_f(fr.left - 8)} '
           f'{_f(fr.top + fr.size / 2)})">predicted</text>']
    parts = []
    for k, (name, pairs) in enumerate(sets.items()):
        color = SET_COLORS[k % len(SET_COLORS)]
        for y, p in pairs:
            p = min(hi, max(lo, p))  # wild predictions sit on the border
            out.append(f'<circle cx="{fr.x(y)}" cy="{fr.y(p)}" r="1.8" fill="{color}" '
                       'fill-opacity="0.55"/>')
        out.append(f'<circle cx="{_f(fr.left + 10)}" cy="{_f(fr.top + 12 + 15 * k)}" r="4" '
                   f'fill="{color}"/><text x="{_f(fr.left + 18)}" y="{_f(fr.top + 16 + 15 * k)}" '
                   f'font-size="11" fill="{INK}">{escape(name)}</text>')
        parts.append(f"{name} {_r2(pairs):.3f}")
    return out, "R² " + " · ".join(parts)


def _range(sol: dict) -> tuple[float, float]:
    ys = [y for pairs in sol["sets"].values() for y, _ in pairs]
    lo, hi = min(ys), max(ys)
    pad = 0.05 * (hi - lo or 1.0)
    return lo - pad, hi + pad


MONO = "ui-monospace, SF Mono, Menlo, Consolas, monospace"
CHAR_W = 0.61  # width of a monospace character per unit of font size


def _bytes(b: float) -> str:
    for unit, div in (("MB", 1e6), ("KB", 1e3)):
        if b >= div:
            v = b / div
            return f"{v:.1f} {unit}" if v < 100 else f"{v:.0f} {unit}"
    return f"{b:.0f} B"


def _seconds(t: float) -> str:
    for unit, mul in (("s", 1), ("ms", 1e3), ("µs", 1e6)):
        if t * mul >= 1:
            return f"{t * mul:.3g} {unit}"
    return f"{t * 1e9:.3g} ns"


def _clip(text: str, width: float, size: float) -> str:
    n = max(4, int(width / (CHAR_W * size)))
    return text if len(text) <= n else text[:n - 1] + "…"


def _mono(x: float, y: float, text: str, size: float = 10.5, fill: str = INK,
          anchor: str = "start") -> str:
    return (f'<text x="{_f(x)}" y="{_f(y)}" font-family="{MONO}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}" xml:space="preserve">{escape(text)}</text>')


def _label(x: float, y: float, text: str, size: float = 12, fill: str = MUTED,
           anchor: str = "start", weight: str = "normal") -> str:
    return (f'<text x="{_f(x)}" y="{_f(y)}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(text)}</text>')


def _cells(x0: float, y0: float, count: int, pitch: float, cols: int, fill: str,
           opacity: float = 1.0, outline: bool = False) -> list[str]:
    side = _f(pitch * 0.78)
    style = (f'fill="none" stroke="{fill}" stroke-width="0.8"' if outline else
             f'fill="{fill}"' + (f' fill-opacity="{opacity:.2f}"' if opacity < 1 else ""))
    return [f'<rect x="{_f(x0 + (k % cols) * pitch)}" y="{_f(y0 + (k // cols) * pitch)}" '
            f'width="{side}" height="{side}" rx="1" {style}/>' for k in range(count)]


# ------------------------------------------------------------- compression
def _compression_unit(original: int) -> int:
    """A round number of bytes per square, so the input fills at most ~240 squares."""
    k = 1
    while True:
        for m in (1, 2, 5):
            if original / (m * k) <= 240:
                return m * k
        k *= 10


def _compression(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    orig, comp = sol["original_bytes"], sol["compressed_bytes"]
    unit = _compression_unit(orig)
    n_orig, n_comp = math.ceil(orig / unit), math.ceil(comp / unit)
    cols = math.ceil(math.sqrt(n_orig))
    pitch = (PLOT - 40) / cols
    x0, y0 = ox + (W - cols * pitch) / 2, TOP + 14
    out = _cells(x0, y0, n_orig, pitch, cols, GRID, outline=True)
    out += _cells(x0, y0, n_comp, pitch, cols, ACCENT_DARK)
    out.append(_label(ox + W / 2, y0 + math.ceil(n_orig / cols) * pitch + 14,
                      f"each square: {_bytes(unit)} · outline: "
                      f"the input, {_bytes(orig)}", 11, anchor="middle"))
    return out, (f"−{100 * (1 - comp / orig):.1f}%: {_bytes(comp)} "
                 f"(ratio {orig / comp:.2f})")


def _compression_band(sol: dict, width: float) -> tuple[float, list[str]]:
    orig = sol["original_bytes"]
    out = [_label(16, 26, f"Input: {sol.get('input', 'data')}, {sol.get('lines', 0):,} lines, "
                  f"{_bytes(orig)}", 15, INK, weight="600")]
    y = 50
    for line in sol.get("head", [])[:4]:
        out.append(_mono(16, y, _clip(line, width - 32, 10.5)))
        y += 16
    out.append(_mono(16, y, "…"))
    unit = _compression_unit(orig)
    n = math.ceil(orig / unit)
    pitch = 9.0
    cols = int((width - 32) // pitch)
    y += 14
    out += _cells(16, y, n, pitch, cols, MUTED, opacity=0.55)
    y += math.ceil(n / cols) * pitch + 16
    refs = " · ".join(f"{k} {_bytes(v)}" for k, v in (sol.get("references") or {}).items())
    out.append(_label(16, y, f"{_bytes(orig)}, one square per {_bytes(unit)}"
                      + (f" · standard tools: {refs}" if refs else ""), 11))
    return y + 14, out


# ---------------------------------------------------------- classification
def _classification(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    hits = sol["test_correct"]
    cols = math.ceil(math.sqrt(len(hits)))
    pitch = (PLOT - 40) / cols
    x0, y0 = ox + (W - cols * pitch) / 2, TOP + 14
    out = [f'<rect x="{_f(x0 + (k % cols) * pitch)}" y="{_f(y0 + (k // cols) * pitch)}" '
           f'width="{_f(pitch * 0.8)}" height="{_f(pitch * 0.8)}" rx="1" '
           f'fill="{GOOD if h == "1" else WARN}"/>' for k, h in enumerate(hits)]
    out.append(_label(ox + W / 2, y0 + math.ceil(len(hits) / cols) * pitch + 14,
                      f"each square: one of {len(hits):,} "
                      "held-out rows · teal right, orange wrong", 11, anchor="middle"))
    acc = hits.count("1") / max(1, len(hits))
    return out, f"accuracy {100 * acc:.1f}% · {sol.get('num_features', '?')} features"


def _classification_band(sol: dict, width: float) -> tuple[float, list[str]]:
    cols = sol["columns"] + ["label"]
    out = [_label(16, 26, f"Data: {sol.get('train_rows', 0):,} training rows, "
                  f"{len(sol['test_correct']):,} held out; columns {', '.join(sol['columns'])}",
                  15, INK, weight="600")]
    cw = min(64.0, (width - 32) / len(cols))
    for j, c in enumerate(cols):
        out.append(_mono(16 + (j + 1) * cw - 8, 50, c, 10.5, MUTED, "end"))
    for i, (row, lab) in enumerate(zip(sol["head"], sol.get("head_labels", []))):
        for j, v in enumerate(list(row) + [lab]):
            out.append(_mono(16 + (j + 1) * cw - 8, 68 + 16 * i,
                             f"{v:.2f}" if j < len(row) else str(v), 10.5, INK, "end"))
    y = 68 + 16 * len(sol["head"])
    out.append(_mono(16 + cw - 8, y, "…", 10.5, INK, "end"))
    out.append(_label(16, y + 22, "The program turns each row into features; a fixed linear "
                      "classifier learns from them and predicts the label.", 11))
    return y + 36, out


# -------------------------------------------------------------------- value
def _value(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    limit, v = sol.get("limit", 100), sol["value"]
    fr = _Frame(ox, 0, limit, 0, 1)
    y = _f(TOP + PLOT / 2)
    out = [f'<line x1="{fr.x(0)}" y1="{y}" x2="{fr.x(limit)}" y2="{y}" stroke="{INK}" '
           'stroke-width="1.5"/>']
    for t in range(0, int(limit) + 1, max(1, int(limit) // 10)):
        out.append(f'<line x1="{fr.x(t)}" y1="{_f(TOP + PLOT / 2 - 4)}" x2="{fr.x(t)}" '
                   f'y2="{_f(TOP + PLOT / 2 + 4)}" stroke="{INK}"/>')
        out.append(_label(float(fr.x(t)), TOP + PLOT / 2 + 20, str(t), 10, anchor="middle"))
    out.append(f'<circle cx="{fr.x(min(max(v, 0), limit))}" cy="{y}" r="7" fill="{ACCENT_DARK}"/>')
    return out, f"value {_num(v)} (at most {_num(limit)})"


# --------------------------------------------- throughput (C++ speed kinds)
# kind: (work per job in the rate's unit, rate unit, what one job is)
def _work(sol: dict) -> tuple[float, str, str]:
    k = sol["kind"]
    if k == "matmul":
        n = sol["n"]
        return 2 * n ** 3 / 1e9, "GFLOP/s", f"one {n}×{n} product"
    if k == "sort":
        n, d = sol["n"], len(sol.get("distributions") or [1])
        return n * d / 1e6, "M values/s", f"{d} × {n:,} values"
    if k == "scan":
        return sol["n"] * 8 / 1e9, "GB/s", f"{sol['n']:,} values"
    return sol["text_mb"], "MB/s", f"{sol['count']:,} numbers"


def _throughput(sol: dict, ox: float, scale: dict) -> tuple[list[str], str]:
    work, unit, job = _work(sol)
    rate, best = scale.get("score"), scale.get("rate_max")
    frac = 1.0 if not rate or not best else min(1.0, rate / best)
    x0, y0, size = ox + 40, TOP + 18, PLOT - 70
    out = []
    if sol["kind"] == "matmul":
        out.append(f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(size)}" height="{_f(size)}" '
                   f'fill="#fff" stroke="{INK}" stroke-width="1.2"/>')
        rows = 64
        done = frac * rows
        full = int(done)
        if full:
            out.append(f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(size)}" '
                       f'height="{_f(size * full / rows)}" fill="{ACCENT}" fill-opacity="0.75"/>')
        if done > full:  # the row being computed
            out.append(f'<rect x="{_f(x0)}" y="{_f(y0 + size * full / rows)}" '
                       f'width="{_f(max(1.5, size * (done - full)))}" '
                       f'height="{_f(size / rows)}" fill="{ACCENT}" fill-opacity="0.75"/>')
        out.append(_label(x0 + size / 2, y0 - 5, "C", 11, anchor="middle"))
    else:
        # the job in processing order: one strip per input, or the data cut into strips
        names = sol.get("distributions") or [""] * 8
        bars = len(names)
        h = min(28.0, size / bars * 0.7)
        step = size / bars
        done = frac * bars
        if not any(names):
            what = "text" if sol["kind"] == "parse" else "array"
            out.append(_label(x0, y0 - 5, f"the whole {what}, in processing order", 10))
        for k, name in enumerate(names):
            y = y0 + k * step
            out.append(f'<rect x="{_f(x0)}" y="{_f(y)}" width="{_f(size)}" height="{_f(h)}" '
                       f'fill="#fff" stroke="{GRID}"/>')
            part = min(1.0, max(0.0, done - k))
            if part > 0:
                out.append(f'<rect x="{_f(x0)}" y="{_f(y)}" width="{_f(max(1.5, size * part))}" '
                           f'height="{_f(h)}" fill="{ACCENT}" fill-opacity="0.75"/>')
            if name:
                out.append(_label(x0 + 4, y + h + 11, name, 10))
    if rate:
        t_job, t_ref = work / rate, work / best
        pct = 100 * frac
        done = "all" if frac >= 1 else (f"{pct:.0f}%" if pct >= 10 else f"{pct:.2g}%")
        out.append(_label(ox + W / 2, TOP + PLOT - 22, f"within {_seconds(t_ref)}, the faster "
                          f"program's time: {done} done", 11, INK, "middle"))
        return out, f"{rate:.4g} {unit} · {_seconds(t_job)} for {job}"
    return out, job


def _matrix(x: float, y: float, side: float, mark: str) -> list[str]:
    out = [f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(side)}" height="{_f(side)}" fill="#fff" '
           f'stroke="{INK}" stroke-width="1.2"/>']
    band = side / 10
    if mark == "row":
        out.append(f'<rect x="{_f(x)}" y="{_f(y + 3 * band)}" width="{_f(side)}" '
                   f'height="{_f(band)}" fill="{ACCENT}" fill-opacity="0.6"/>')
    elif mark == "col":
        out.append(f'<rect x="{_f(x + 6 * band)}" y="{_f(y)}" width="{_f(band)}" '
                   f'height="{_f(side)}" fill="{ACCENT}" fill-opacity="0.6"/>')
    else:
        out.append(f'<rect x="{_f(x + 6 * band)}" y="{_f(y + 3 * band)}" width="{_f(band)}" '
                   f'height="{_f(band)}" fill="{ACCENT_DARK}"/>')
    return out


def _matmul_band(sol: dict, width: float) -> tuple[float, list[str]]:
    n = sol["n"]
    side = 74.0
    x = 16.0
    out = [_label(16, 26, f"C = A · B for {n} × {n} float32 matrices", 15, INK, weight="600")]
    for k, (name, mark) in enumerate((("C", "cell"), ("A", "row"), ("B", "col"))):
        out += _matrix(x, 44, side, mark)
        out.append(_label(x + side / 2, 44 + side + 14, name, 12, INK, "middle"))
        x += side
        if k < 2:
            out.append(_label(x + 14, 44 + side / 2 + 6, "=" if k == 0 else "·", 20, INK,
                              "middle"))
            x += 28
    out.append(_label(16, 44 + side + 36, f"each element of C: a row of A times a column of B; "
                      f"one product = 2n³ = {2 * n ** 3 / 1e9:.3g} GFLOP · "
                      f"{sol.get('cores', '')}", 11))
    return 44 + side + 50, out


def _lcg_values(seed: int, count: int) -> list[float]:
    s, out = seed, []
    for _ in range(count):
        s = (s * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        out.append((s >> 11) / float(1 << 53))
    return out


def _sort_shape(name: str, count: int) -> list[float]:
    """An illustration of a distribution's shape (not its data)."""
    v = _lcg_values(len(name) * 7919 + 1, count)
    if name.startswith("dups"):
        v = [round(x * 4) / 4 for x in v]
    if name in ("sorted", "nearly_sorted"):
        v.sort()
    if name == "reverse":
        v.sort(reverse=True)
    if name == "nearly_sorted":
        v[3], v[count - 6] = v[count - 6], v[3]
    return v


def _sort_band(sol: dict, width: float) -> tuple[float, list[str]]:
    names = sol.get("distributions") or []
    out = [_label(16, 26, f"Sort {sol['n']:,} uint32 values, for each of {len(names)} inputs",
                  15, INK, weight="600")]
    cw = min(110.0, (width - 32) / max(1, len(names)))
    bars = 24
    for k, name in enumerate(names):
        x = 16 + k * cw
        bw = (cw - 14) / bars
        for j, v in enumerate(_sort_shape(name, bars)):
            h = 6 + 46 * v
            out.append(f'<rect x="{_f(x + j * bw)}" y="{_f(96 - h)}" width="{_f(bw * 0.8)}" '
                       f'height="{_f(h)}" fill="{MUTED}" fill-opacity="0.6"/>')
        out.append(_label(x, 112, name, 10.5, INK))
    out.append(_label(16, 134, "→ ascending, element for element identical to std::sort; "
                      "one core, measured in CPU time", 11))
    return 148, out


def _scan_band(sol: dict, width: float) -> tuple[float, list[str]]:
    head = sol.get("head") or []
    cw = 104.0
    k = max(1, min(len(head), int((width - 140) // cw)))
    out = [_label(16, 26, f"One pass over {sol['n']:,} int64 values: prefix sum and "
                  "histogram", 15, INK, weight="600")]
    sums, total = [], 0
    for v in head[:k]:
        total += v
        sums.append(total)
    for r, (name, vals) in enumerate((("in", head[:k]), ("out", sums),
                                      ("in & 255", [v & 255 for v in head[:k]]))):
        y = 52 + 18 * r
        out.append(_mono(16, y, name, 10.5, MUTED))
        for j, v in enumerate(vals):
            out.append(_mono(84 + (j + 1) * cw, y, f"{v:,}", 10.5, INK, "end"))
        out.append(_mono(84 + k * cw + 8, y, "…"))
    out.append(_label(16, 52 + 18 * 3 + 6, "out[i] = in[0] + … + in[i]; hist[b] counts the "
                      "values whose low byte is b · one core, measured in CPU time", 11))
    return 52 + 18 * 3 + 20, out


def _parse_band(sol: dict, width: float) -> tuple[float, list[str]]:
    head = sol.get("head") or []
    out = [_label(16, 26, f"Parse {sol['count']:,} decimal numbers ({sol['text_mb']:g} MB of "
                  "text) into doubles", 15, INK, weight="600")]
    per_col = 4
    colw = min(380.0, (width - 32) / 2)
    for i, text in enumerate(head[:per_col * (2 if width > 600 else 1)]):
        x, y = 16 + (i // per_col) * colw, 50 + 16 * (i % per_col)
        out.append(_mono(x, y, _clip(text, colw * 0.45, 10.5)))
        out.append(_mono(x + colw * 0.48, y, "→ " + repr(float(text)), 10.5, ACCENT_DARK))
    y = 50 + 16 * per_col
    out.append(_mono(16, y, "…"))
    out.append(_label(16, y + 20, "every value bit-identical to std::strtod (note "
                      "9007199254740993 and 4.9e-324) · one core, measured in CPU time", 11))
    return y + 34, out


KINDS = {"circles": _circles, "heilbronn": _heilbronn, "sphere_points": _sphere_points,
         "tour": _tour, "bins": _bins, "cut": _cut, "regression": _regression,
         "compression": _compression, "classification": _classification, "value": _value,
         "matmul": _throughput, "sort": _throughput, "scan": _throughput, "parse": _throughput}
# A problem band drawn across the full width above the panels, for kinds whose panels
# alone do not show what the problem is.
BANDS = {"compression": _compression_band, "classification": _classification_band,
         "matmul": _matmul_band, "sort": _sort_band, "scan": _scan_band, "parse": _parse_band}


def shared_scale(solutions: list[dict]) -> dict:
    """Scales both panels share, so they can be compared at a glance."""
    scale: dict = {}
    for s in solutions:
        if s["kind"] == "circles":
            scale["rmax"] = max([scale.get("rmax", 0)] + [r for _, _, r in s["circles"]])
        elif s["kind"] == "bins":
            scale["bins"] = max(scale.get("bins", 0), len(s["bins"]))
        elif s["kind"] == "regression":
            lo, hi = _range(s)
            old = scale.get("range")
            scale["range"] = (min(lo, old[0]), max(hi, old[1])) if old else (lo, hi)
    return scale


def drawable(sol) -> bool:
    return isinstance(sol, dict) and sol.get("kind") in KINDS


def figure(panels: list[tuple], description: str) -> str:
    """One SVG with a panel per (title, solution[, score]), side by side, below the
    problem band of their kind if it has one. ``score`` is the recorded score of the
    program, which speed kinds draw (a fresh measurement would differ)."""
    panels = [(p[0], p[1], p[2] if len(p) > 2 else None) for p in panels]
    scale = shared_scale([s for _, s, _ in panels])
    scores = [sc for _, _, sc in panels if isinstance(sc, (int, float))]
    if scores:
        scale["rate_max"] = max(scores)
    width = len(panels) * W + (len(panels) - 1) * GAP
    band = BANDS.get(panels[0][1]["kind"])
    band_h, band_body = band(panels[0][1], width) if band else (0, [])
    top = band_h + GAP if band else 0
    height = top + H
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {_f(height)}" '
           f'width="{width}" height="{_f(height)}" role="img" '
           f'aria-label="{escape(description, {chr(34): "&quot;"})}" '
           'font-family="system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">',
           "<!-- Generated by `omega docs` from bench/results/*.json. Do not edit by hand. -->",
           f"<title>{escape(description)}</title>"]
    if band:
        out.append(f'<rect x="0.5" y="0.5" width="{width - 1}" height="{_f(band_h - 1)}" '
                   f'rx="10" fill="{BG}" stroke="{BORDER}"/>')
        out += band_body
        out.append(f'<g transform="translate(0 {_f(top)})">')
    for k, (title, sol, score) in enumerate(panels):
        ox = k * (W + GAP)
        body, caption = KINDS[sol["kind"]](sol, ox, dict(scale, score=score))
        out.append(f'<rect x="{ox + 0.5}" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" '
                   f'fill="{BG}" stroke="{BORDER}"/>')
        out.append(f'<text x="{ox + 16}" y="26" font-size="15" font-weight="600" '
                   f'fill="{INK}">{escape(title)}</text>')
        out.append(f'<text x="{ox + 16}" y="46" font-size="12" fill="{MUTED}">'
                   f'{escape(caption)}</text>')
        out += body
    if band:
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"

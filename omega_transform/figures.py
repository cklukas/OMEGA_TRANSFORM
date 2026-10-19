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


KINDS = {"circles": _circles, "heilbronn": _heilbronn, "sphere_points": _sphere_points,
         "tour": _tour, "bins": _bins, "cut": _cut, "regression": _regression}


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


def figure(panels: list[tuple[str, dict]], description: str) -> str:
    """One SVG with a panel per (title, solution), side by side."""
    scale = shared_scale([s for _, s in panels])
    width = len(panels) * W + (len(panels) - 1) * GAP
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {H}" '
           f'width="{width}" height="{H}" role="img" aria-label="{escape(description, {chr(34): "&quot;"})}" '
           'font-family="system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif">',
           "<!-- Generated by `omega docs` from bench/results/*.json. Do not edit by hand. -->",
           f"<title>{escape(description)}</title>"]
    for k, (title, sol) in enumerate(panels):
        ox = k * (W + GAP)
        body, caption = KINDS[sol["kind"]](sol, ox, scale)
        out.append(f'<rect x="{ox + 0.5}" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" '
                   f'fill="{BG}" stroke="{BORDER}"/>')
        out.append(f'<text x="{ox + 16}" y="26" font-size="15" font-weight="600" '
                   f'fill="{INK}">{escape(title)}</text>')
        out.append(f'<text x="{ox + 16}" y="46" font-size="12" fill="{MUTED}">'
                   f'{escape(caption)}</text>')
        out += body
    out.append("</svg>")
    return "\n".join(out) + "\n"

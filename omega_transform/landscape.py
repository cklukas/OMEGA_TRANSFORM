# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""OpenRouter model landscape: community usage against price.

OpenRouter's public rankings page lists the models with the most tokens in
the current period; its model API lists every model's price. A snapshot joins
the two and is written as a JSON file meant to be committed
(``bench/openrouter/``); the documentation page is rendered from the newest
snapshot, so the published tables and charts always come from committed data.

The rankings are read from data embedded in the rankings web page, which is
not a documented API: when its layout changes, the snapshot fails with a clear
message instead of producing wrong numbers. The page covers the last seven
days. It also lists, per model, the input price the community actually paid on
average, which is lower than the list price because cached input is cheaper;
the snapshot keeps it to estimate what the models earned.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from .llm import OPENROUTER_URL, urlopen

RANKINGS_URL = "https://openrouter.ai/rankings"
_ROW = re.compile(
    r'\{"date":"([^"]+)","model_permaslug":"([^"]+)","variant":"([^"]+)",'
    r'"total_completion_tokens":(\d+),"total_prompt_tokens":(\d+)[^{}]*?"count":(\d+)')
_WEIGHTED = re.compile(r'"weightedInputPrices":(\{[^{}]*\})')
#: days in an average month, per day of a week: weekly figures times this are monthly
MONTH_PER_WEEK = 365.25 / 12 / 7
MIN_RADIUS, MAX_RADIUS = 4.0, 22.0  # pixels, of the dots in the cost chart


class SnapshotError(Exception):
    pass


def _get(url: str) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (omega-transform)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_rankings(html: str) -> list[dict]:
    text = html.replace('\\"', '"')
    rows = [{"date": d[:10], "slug": s, "variant": v, "completion_tokens": int(c),
             "prompt_tokens": int(p), "requests": int(n)}
            for d, s, v, c, p, n in _ROW.findall(text)]
    if not rows:
        raise SnapshotError("no model rankings found on the OpenRouter rankings page; "
                            "its layout may have changed")
    return rows


def parse_weighted_prices(html: str) -> dict[str, float]:
    """{model permaslug: average input price paid, dollars per million tokens};
    empty when the page does not have them."""
    m = _WEIGHTED.search(html.replace('\\"', '"'))
    return {k: float(v) for k, v in json.loads(m.group(1)).items()} if m else {}


def take_snapshot() -> dict:
    html = _get(RANKINGS_URL)
    rankings = parse_rankings(html)
    weighted = parse_weighted_prices(html)
    models = json.loads(_get(f"{OPENROUTER_URL}/models"))["data"]
    # Variants (":batch", ":free", ...) share the canonical slug; prefer the plain model.
    by_slug: dict[str, dict] = {}
    for m in sorted(models, key=lambda m: (":" in m["id"], m["id"])):
        by_slug.setdefault(m.get("canonical_slug") or m["id"], m)
    ids = {m["id"] for m in models}
    out = []
    for r in sorted(rankings, key=lambda r: -(r["prompt_tokens"] + r["completion_tokens"])):
        m = by_slug.get(r["slug"], {})
        mid = m.get("id") or re.sub(r"-\d{8}$", "", r["slug"])
        pricing = m.get("pricing") or {}
        p_in = float(pricing.get("prompt") or 0) * 1e6  # dollars per million tokens
        p_out = float(pricing.get("completion") or 0) * 1e6
        p_cache = pricing.get("input_cache_read")
        p_cache = float(p_cache) * 1e6 if p_cache else None
        p_paid = weighted.get(r["slug"])
        if r["variant"] == "free":  # this usage was of the free variant: it cost nothing
            mid, p_in, p_out, p_cache, p_paid = f"{mid}:free", 0.0, 0.0, None, 0.0
        tokens = r["prompt_tokens"] + r["completion_tokens"]
        effective = (p_in * r["prompt_tokens"] + p_out * r["completion_tokens"]) / tokens \
            if tokens else None
        out.append({
            "id": mid, "variant": r["variant"], "date": r["date"],
            "tokens": tokens, "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"], "requests": r["requests"],
            "price_in": round(p_in, 4), "price_out": round(p_out, 4),
            "price_cache_read": None if p_cache is None else round(p_cache, 4),
            "price_in_paid": None if p_paid is None else round(p_paid, 6),
            "effective_price": round(effective, 4) if effective is not None else None,
            "free_variant": f"{mid}:free" in ids or r["variant"] == "free",
            "context": m.get("context_length"),
            "reasoning": "reasoning" in (m.get("supported_parameters") or []),
            "listed": bool(m),
        })
    # Every model's price, so that prices can be followed over time, not only those
    # of the models that are popular on the day: [input, output, cached input].
    prices = {}
    for m in models:
        p = m.get("pricing") or {}
        try:
            row = [float(p.get(k) or 0) * 1e6 for k in ("prompt", "completion")]
        except (TypeError, ValueError):
            continue
        if min(row) < 0:  # negative prices mark routers whose price varies
            continue
        cache = p.get("input_cache_read")
        prices[m["id"]] = [round(row[0], 4), round(row[1], 4),
                           round(float(cache) * 1e6, 4) if cache else None]
    return {"taken": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": RANKINGS_URL,
            "models": out, "prices": prices}


def save_snapshot(snap: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snap['taken'][:10]}.json"
    path.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8", newline="\n")
    return path


def load_snapshots(directory: Path) -> list[dict]:
    """All snapshots, oldest first."""
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(directory.glob("*.json"))]


def latest_snapshot(directory: Path) -> dict | None:
    files = sorted(directory.glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def snapshot_prices(snap: dict) -> dict[str, tuple[float, float]]:
    """{model id: (input, output) list price in dollars per million tokens} of one
    snapshot: all models where the snapshot has them, else its ranked models."""
    if "prices" in snap:
        return {k: (v[0], v[1]) for k, v in snap["prices"].items()}
    return {m["id"]: (m["price_in"], m["price_out"]) for m in snap["models"]
            if m["listed"] and m["variant"] != "free"}


# Vendors by the prefix of OpenRouter's model ids: (name, country of the headquarters).
VENDORS = {
    "anthropic": ("Anthropic", "USA"), "openai": ("OpenAI", "USA"), "google": ("Google", "USA"),
    "meta": ("Meta", "USA"), "meta-llama": ("Meta", "USA"), "x-ai": ("xAI", "USA"),
    "nvidia": ("NVIDIA", "USA"), "amazon": ("Amazon", "USA"), "microsoft": ("Microsoft", "USA"),
    "ibm-granite": ("IBM", "USA"), "arcee-ai": ("Arcee AI", "USA"),
    "inception": ("Inception", "USA"), "liquid": ("Liquid AI", "USA"),
    "deepseek": ("DeepSeek", "China"), "z-ai": ("Z.ai", "China"),
    "qwen": ("Alibaba (Qwen)", "China"), "moonshotai": ("Moonshot AI", "China"),
    "minimax": ("MiniMax", "China"), "xiaomi": ("Xiaomi", "China"),
    "tencent": ("Tencent", "China"), "stepfun": ("StepFun", "China"),
    "stepfun-ai": ("StepFun", "China"), "meituan": ("Meituan", "China"),
    "bytedance-seed": ("ByteDance", "China"), "baidu": ("Baidu", "China"),
    "inclusionai": ("Ant Group", "China"), "mistralai": ("Mistral AI", "France"),
    "poolside": ("Poolside", "France"), "cohere": ("Cohere", "Canada"),
    "sakana": ("Sakana AI", "Japan"), "aleph-alpha": ("Aleph Alpha", "Germany"),
}


def vendor(model_id: str) -> tuple[str, str]:
    """(vendor, country) of an OpenRouter model id; unknown prefixes keep their name."""
    prefix = model_id.split("/")[0]
    return VENDORS.get(prefix, (prefix, "unknown"))


def revenue(m: dict) -> float | None:
    """Estimated dollars a model earned in the snapshot's week: its input tokens at the
    input price the community paid on average (cached input is cheaper), or at the
    list price when that is unknown, plus its output tokens at the list price. None
    for unlisted models, whose price is unknown."""
    if not m["listed"]:
        return None
    paid = m.get("price_in_paid")
    p_in = m["price_in"] if paid is None else paid
    return (p_in * m["prompt_tokens"] + m["price_out"] * m["completion_tokens"]) / 1e6


def cache_share(m: dict) -> float | None:
    """The share of input tokens that must have been cache hits for the paid average
    price; None when it cannot be told (no cache price, or prices changed)."""
    paid, full, cached = m.get("price_in_paid"), m["price_in"], m.get("price_cache_read")
    if paid is None or cached is None or full <= cached or not cached <= paid <= full:
        return None
    return (full - paid) / (full - cached)


def _money(v: float) -> str:
    """Dollars, compact: $12.3M, $456k, $789."""
    if v >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v / 1e3:,.0f}k" if v >= 1e3 else f"${v:,.0f}"


def _short(model_id: str) -> str:
    """A short label for charts: model name without vendor and date."""
    name = model_id.split("/")[-1]
    return re.sub(r"[^A-Za-z0-9.\-]", "", name)[:24]


def _price(v: float) -> str:
    return "-" if v != v else f"{v:.2f}"  # NaN: unknown price


def render(snap: dict, benchmarked: set[str] | None = None,
           history: list[dict] | None = None) -> str:
    models = snap["models"]
    benchmarked = benchmarked or set()
    parts = ["<!-- Copyright (c) 2026 by C. Klukas -->\n"
             "<!-- SPDX-License-Identifier: GPL-3.0-only -->\n"
             "<!-- Generated by `omega report` from bench/openrouter/*.json. "
             "Do not edit by hand. -->\n\n# OpenRouter models\n\n",
             "Which cloud models does the community use most, and what do they cost? "
             "The table joins OpenRouter's public usage rankings with its price list "
             f"(snapshot of {snap['taken'][:10]}; rerun `omega openrouter-snapshot` "
             "to refresh). See [Cloud models](cloud-models.md) for using them.\n\n",
             "Usage measures popularity, not quality: it also reflects price, free "
             "periods and the defaults of widely used tools. The **effective price** "
             "weights a model's input and output price by how the community actually "
             "used it (most traffic is input).\n\n",
             "| # | Model | Tokens (B) | Requests (M) | In $/M | Out $/M | Effective $/M "
             "| Free variant | Reasoning | Context |\n"
             "|--:|---|--:|--:|--:|--:|--:|---|---|--:|\n"]
    for i, m in enumerate(models, 1):
        name = m["id"]
        if m["id"] in benchmarked:
            name += " ★"
        if not m["listed"]:
            name += " (unlisted)"
            m = dict(m, effective_price=None, price_in=float("nan"), price_out=float("nan"))
        eff = "-" if m["effective_price"] is None else f"{m['effective_price']:.3f}"
        parts.append(
            f"| {i} | {name} | {m['tokens'] / 1e9:,.0f} | {m['requests'] / 1e6:,.1f} "
            f"| {_price(m['price_in'])} | {_price(m['price_out'])} | {eff} "
            f"| {'yes' if m['free_variant'] else '-'} | {'yes' if m['reasoning'] else '-'} "
            f"| {(m['context'] or 0) // 1000}k |\n")
    if benchmarked & {m["id"] for m in models}:
        parts.append("\n★ benchmarked by OMEGA TRANSFORM, see [Model benchmarks](benchmarks.md).\n")

    labels = ", ".join(f'"{_short(m["id"])}"' for m in models)
    toks = [m["tokens"] / 1e9 for m in models]
    parts.append(f"\n```mermaid\nxychart-beta\n    title \"Usage (billion tokens)\"\n"
                 f"    x-axis [{labels}]\n    y-axis \"B tokens\" 0 --> {max(toks) * 1.1:.0f}\n"
                 f"    bar [{', '.join(f'{v:.0f}' for v in toks)}]\n```\n")
    prices = [(m["effective_price"] or 0) if m["listed"] else 0 for m in models]
    parts.append(f"\n```mermaid\nxychart-beta\n    title \"Effective price (dollars per million "
                 f"tokens)\"\n    x-axis [{labels}]\n    y-axis \"$/M tokens\" 0 --> "
                 f"{max(prices + [0.01]) * 1.1:.3g}\n"
                 f"    bar [{', '.join(f'{v:.3f}' for v in prices)}]\n```\n")

    # Cost against usage, both on log scales: the quadrant chart plots points in [0, 1].
    pts = [(m, m["effective_price"], m["tokens"]) for m in models
           if m["listed"] and m["effective_price"]]
    if len(pts) >= 2:
        lp = [math.log10(p) for _, p, _ in pts]
        lt = [math.log10(t) for _, _, t in pts]
        p0, p1 = min(lp), max(lp)
        t0, t1 = min(lt), max(lt)
        top = max(revenue(m) or 0 for m, _, _ in pts) or 1.0
        lines = []
        seen = set()
        shown = []
        for (m, p, t), x, y in zip(pts, lp, lt):
            label = _short(m["id"])
            if label in seen:
                continue
            seen.add(label)
            shown.append(m)
            xn = 0.05 + 0.9 * (x - p0) / ((p1 - p0) or 1)
            yn = 0.05 + 0.9 * (y - t0) / ((t1 - t0) or 1)
            # The dot's area, not its radius, grows with the revenue.
            radius = MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * math.sqrt((revenue(m) or 0) / top)
            lines.append(f"    {label}: [{xn:.3f}, {yn:.3f}] radius: {radius:.1f}")
        parts.append(
            "\n### Cost against usage\n\nBoth axes are logarithmic, from the cheapest to the "
            f"most expensive model (${10 ** p0:.3g} to ${10 ** p1:.3g} per million tokens) and "
            f"from the least to the most used ({10 ** t0 / 1e9:,.0f} to {10 ** t1 / 1e9:,.0f} "
            "billion tokens in the week). A dot's area grows with the model's estimated "
            f"revenue (the largest: {_money(top * MONTH_PER_WEEK)} a month; see the tables "
            "below). Free and unlisted models are not shown.\n\n"
            "```mermaid\nquadrantChart\n    title Cost against usage\n"
            "    x-axis Cheap --> Expensive\n    y-axis Less used --> Most used\n"
            "    quadrant-1 Expensive and popular\n    quadrant-2 Cheap and popular\n"
            "    quadrant-3 Cheap niche\n    quadrant-4 Expensive niche\n"
            + "\n".join(lines) + "\n```\n")
        parts.append(_revenue_tables(snap, shown))
    parts.append(_price_history(snap, history or [snap]))
    return "".join(parts)


def _blended(price: tuple[float, float], input_share: float) -> float:
    return price[0] * input_share + price[1] * (1 - input_share)


def _price_history(snap: dict, history: list[dict]) -> str:
    """List prices of today's popular paid models in every snapshot, and a line chart
    for each whose price changed, once there are enough snapshots to draw a line."""
    dated = {}  # one price list per day: the last snapshot of a day wins
    for s in history:
        dated[s["taken"][:10]] = snapshot_prices(s)
    dates = sorted(dated)
    tracked = [m for m in snap["models"] if m["listed"] and m["variant"] != "free"]
    if not tracked:
        return ""
    parts = ["\n### Price history\n\n"
             "List prices in dollars per million tokens (input / output) in each snapshot, "
             "for the paid models of the current rankings; \"-\" where a snapshot does not "
             "have the model (snapshots before 2026-10-31 hold only the models ranked that "
             "week). Rerun `omega openrouter-snapshot` from time to time to extend it.\n\n"
             "| Model | " + " | ".join(dates) + " | Change |\n"
             "|---|" + "--:|" * len(dates) + "---|\n"]
    charts: dict[str, list[str]] = {}  # per vendor
    for m in tracked:
        series = [dated[d].get(m["id"]) for d in dates]
        known = [p for p in series if p is not None]
        cells = ["-" if p is None else f"{p[0]:.3g} / {p[1]:.3g}" for p in series]
        share = m["prompt_tokens"] / m["tokens"] if m["tokens"] else 0.9
        change = "-"
        if len(known) >= 2 and known[0] != known[-1]:
            a, b = _blended(known[0], share), _blended(known[-1], share)
            change = f"{(b - a) / a:+.0%}" if a else "new price"
        if len(known) >= 2:
            pts = [(d, _blended(p, share)) for d, p in zip(dates, series) if p is not None]
            top = max(v for _, v in pts) or 1.0
            charts.setdefault(vendor(m["id"])[0], []).append(
                f"\n```mermaid\nxychart-beta\n    title \"{_short(m['id'])}: $ per million "
                f"tokens at {share:.0%} input\"\n"
                f"    x-axis [{', '.join(f'\"{d}\"' for d, _ in pts)}]\n"
                f"    y-axis \"$/M tokens\" 0 --> {top * 1.15:.3g}\n"
                f"    line [{', '.join(f'{v:.4g}' for _, v in pts)}]\n```\n")
        parts.append(f"| {m['id']} | " + " | ".join(cells) + f" | {change} |\n")
    parts.append("\n**Change** compares the first and the last known price, blended at the "
                 "model's share of input tokens in the current week.\n")
    if charts:
        parts.append("\n#### Prices over time, by vendor\n\nEach model's price as one "
                     "number per million tokens, blended at its share of input tokens in the "
                     "current week; the x-axis lists the snapshot dates, evenly spaced rather "
                     "than to scale.\n")
        for who in sorted(charts):
            parts.append(f"\n##### {who}\n")
            parts.extend(charts[who])
    return "".join(parts)


def _revenue_tables(snap: dict, shown: list[dict]) -> str:
    """The models of the cost chart with their estimated revenue, then the revenue
    summed by vendor and by country."""
    models = snap["models"]
    total = sum(revenue(m) or 0 for m in shown) or 1.0
    shares = sorted(m["prompt_tokens"] / m["tokens"] for m in models if m["tokens"])
    median_in = shares[len(shares) // 2]
    paid_known = sum(m.get("price_in_paid") is not None for m in shown)
    parts = [
        "\n### Estimated revenue\n\n"
        "What the models earned through OpenRouter in the week, estimated from the "
        "snapshot: input tokens times the input price the community paid on average, plus "
        "output tokens times the output list price. Monthly figures are the week times "
        f"{MONTH_PER_WEEK:.2f}. The split between input and output is not assumed: "
        "OpenRouter reports both for each model. Input dominates everywhere, "
        f"{shares[0]:.1%} to {shares[-1]:.1%} of the tokens (median {median_in:.1%}), "
        "typical of coding agents that send their whole context with every request. "
        "Most of that input is cached, and cached input costs a fraction of the list price; "
        "OpenRouter's rankings page lists the average input price that was actually paid "
        f"(known for {paid_known} of {len(shown)} models here; where it is not, the list "
        "price is used, an upper bound). **Cache hits** is the share of input that must "
        "have come from the cache to give that average.\n\n"
        "These are rough estimates: they cover OpenRouter only (not the vendors' own APIs "
        "or other resellers), list prices may have changed during the week, and the money "
        "is shared between OpenRouter and the provider that served the model.\n\n"
        "| # | Model | Vendor | Country | Tokens (B/week) | Requests (M/week) "
        "| k tokens/request | Input share | In $/M list | In $/M paid | Cache hits "
        "| Out $/M | Revenue/week | Revenue/month | Share |\n"
        "|--:|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|\n"]
    ranked = sorted(shown, key=lambda m: -(revenue(m) or 0))
    for i, m in enumerate(ranked, 1):
        who, where = vendor(m["id"])
        rev = revenue(m) or 0
        paid = m.get("price_in_paid")
        hits = cache_share(m)
        parts.append(
            f"| {i} | {m['id']} | {who} | {where} | {m['tokens'] / 1e9:,.0f} "
            f"| {m['requests'] / 1e6:,.1f} | {m['tokens'] / max(m['requests'], 1) / 1e3:,.0f} "
            f"| {m['prompt_tokens'] / m['tokens']:.1%} | {m['price_in']:.3f} "
            f"| {'-' if paid is None else f'{paid:.3f}'} "
            f"| {'-' if hits is None else f'{hits:.0%}'} | {m['price_out']:.2f} "
            f"| {_money(rev)} | {_money(rev * MONTH_PER_WEEK)} | {rev / total:.1%} |\n")

    def summary(key, title: str, head: str) -> str:
        groups: dict[str, dict] = {}
        for m in models:
            if not m["listed"]:
                continue
            g = groups.setdefault(key(m), {"models": 0, "tokens": 0, "free": 0, "rev": 0.0,
                                           "names": set()})
            g["models"] += 1
            g["tokens"] += m["tokens"]
            g["free"] += m["tokens"] if m["variant"] == "free" else 0
            g["rev"] += revenue(m) or 0
            g["names"].add(vendor(m["id"])[0])
        out = [f"\n### {title}\n\n{head}\n\n"
               "| # | {0} | Models | Tokens (B/week) | Free tokens | Revenue/month "
               "| Share | Revenue per M tokens |\n"
               "|--:|---|--:|--:|--:|--:|--:|--:|\n"]
        for i, (name, g) in enumerate(sorted(groups.items(), key=lambda kv: -kv[1]["rev"]), 1):
            out.append(
                f"| {i} | {name} | {g['models']} | {g['tokens'] / 1e9:,.0f} "
                f"| {g['free'] / g['tokens']:.0%} | {_money(g['rev'] * MONTH_PER_WEEK)} "
                f"| {g['rev'] / total:.1%} | ${g['rev'] / g['tokens'] * 1e6:.3f} |\n")
        return "".join(out)

    unlisted = [m["id"] for m in models if not m["listed"]]
    note = (f" Unlisted models ({', '.join(unlisted)}) are left out: their price is unknown."
            if unlisted else "")
    by_vendor = summary(lambda m: vendor(m["id"])[0], "Revenue by vendor",
                        "All listed models of the rankings, summed by vendor; free variants "
                        "count with their tokens but earn nothing." + note)
    by_country = summary(lambda m: vendor(m["id"])[1], "Revenue by country",
                         "The same, summed by the country of the vendor's headquarters.")
    return "".join(parts) + by_vendor.replace("{0}", "Vendor") + \
        by_country.replace("{0}", "Country")

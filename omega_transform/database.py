# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""Program database: every attempt, the island/MAP-Elites archive, and the
lessons notebook, in one SQLite file per run.

Every attempt is stored, failures included, because the prompt context is
built from this history: the LLM sees which ideas were tried on its branch and
what they scored or why they broke. Only successful programs (status ``ok``)
can become niche elites or parents, apart from repair mode, which takes a
failed attempt and asks the LLM to fix it.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import Feature

OK = "ok"
# Failure statuses, from earliest to latest in the pipeline.
FAILED = ("llm_error", "edit_error", "duplicate", "compile_error", "runtime_error",
          "timeout", "invalid", "filtered")
# A sweep variant that was evaluated but not kept (only the best variant of a
# sweep enters the population); hidden from prompts, kept for deduplication.
SWEPT = "swept"

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs(
  id TEXT PRIMARY KEY, parent_id TEXT, island INTEGER, generation INTEGER,
  iteration INTEGER, created REAL, code TEXT, code_hash TEXT, diff TEXT,
  idea TEXT, model TEXT, mode TEXT, status TEXT, score REAL, metrics TEXT,
  features TEXT, feedback TEXT, stage TEXT, eval_seconds REAL, llm_seconds REAL,
  cell TEXT);
CREATE INDEX IF NOT EXISTS ix_hash ON programs(code_hash);
CREATE INDEX IF NOT EXISTS ix_island ON programs(island, status, score);
CREATE INDEX IF NOT EXISTS ix_parent ON programs(parent_id);
CREATE TABLE IF NOT EXISTS elites(
  island INTEGER, cell TEXT, program_id TEXT, PRIMARY KEY(island, cell));
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, iteration INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS llm_calls(
  program_id TEXT, created REAL, model TEXT, total_seconds REAL, load_seconds REAL,
  prompt_tokens INTEGER, prompt_seconds REAL, gen_tokens INTEGER, gen_seconds REAL,
  stopped INTEGER, cost_usd REAL, provider TEXT);
CREATE TABLE IF NOT EXISTS solutions(program_id TEXT PRIMARY KEY, data TEXT);
"""

# A drawable solution larger than this (as JSON) is not stored.
MAX_SOLUTION_CHARS = 200_000


def code_hash(code: str) -> str:
    norm = "\n".join(ln.rstrip() for ln in code.strip().splitlines())
    return hashlib.sha1(norm.encode()).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Program:
    id: str
    code: str
    parent_id: str | None = None
    island: int = 0
    generation: int = 0
    iteration: int = 0
    created: float = field(default_factory=time.time)
    code_hash: str = ""
    diff: str = ""
    idea: str = ""
    model: str = ""
    mode: str = ""
    status: str = OK
    score: float | None = None
    metrics: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    feedback: str = ""
    stage: str = ""
    eval_seconds: float = 0.0
    llm_seconds: float = 0.0
    cell: str = ""
    # The evaluator's drawable "solution"; kept in a table of its own (``solution()``),
    # so it is not loaded with every program.
    solution: dict | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == OK and self.score is not None


_COLS = [f for f in Program.__dataclass_fields__ if f != "solution"]


def _row_to_program(row: sqlite3.Row) -> Program:
    d = dict(row)
    d["metrics"] = json.loads(d["metrics"] or "{}")
    d["features"] = json.loads(d["features"] or "{}")
    return Program(**d)


def feature_cell(features: dict, specs: list[Feature]) -> str:
    idx = []
    for f in specs:
        v = features.get(f.name)
        if v is None or not isinstance(v, (int, float)) or math.isnan(v):
            idx.append("_")
            continue
        lo, hi, x = f.min, f.max, float(v)
        if f.log:
            lo, hi, x = math.log(max(lo, 1e-12)), math.log(max(hi, 1e-12)), math.log(max(x, 1e-12))
        t = 0.0 if hi <= lo else (x - lo) / (hi - lo)
        idx.append(str(min(f.bins - 1, max(0, int(t * f.bins)))))
    return "|".join(idx)


class Database:
    def __init__(self, path: str | Path, islands: int, features: list[Feature]):
        self.path = Path(path)
        self.islands = islands
        self.features = features
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(llm_calls)")}
        for col, kind in (("cost_usd", "REAL"), ("provider", "TEXT")):
            if col not in cols:  # run databases from before these columns existed
                self.conn.execute(f"ALTER TABLE llm_calls ADD COLUMN {col} {kind}")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- basics
    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def get(self, pid: str) -> Program | None:
        rows = self._q("SELECT * FROM programs WHERE id=?", (pid,))
        return _row_to_program(rows[0]) if rows else None

    def find(self, prefix: str) -> Program | None:
        rows = self._q("SELECT * FROM programs WHERE id LIKE ? LIMIT 2", (prefix + "%",))
        return _row_to_program(rows[0]) if len(rows) == 1 else None

    def meta(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

    def count(self, status: str | None = None) -> int:
        if status is None:
            return self._q("SELECT COUNT(*) FROM programs")[0][0]
        return self._q("SELECT COUNT(*) FROM programs WHERE status=?", (status,))[0][0]

    def status_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self._q("SELECT status, COUNT(*) FROM programs GROUP BY status")}

    def max_iteration(self) -> int:
        v = self._q("SELECT MAX(iteration) FROM programs")[0][0]
        return -1 if v is None else v

    def by_hash(self, h: str) -> Program | None:
        rows = self._q("SELECT * FROM programs WHERE code_hash=? ORDER BY created LIMIT 1", (h,))
        return _row_to_program(rows[0]) if rows else None

    # ------------------------------------------------------------ insertion
    def add(self, p: Program) -> bool:
        """Store ``p``; returns True if it became a new elite of its niche."""
        if not p.code_hash:
            p.code_hash = code_hash(p.code)
        p.created = time.time()  # completion time: orders the journal by when results arrived
        p.cell = feature_cell(p.features, self.features) if p.ok else ""
        vals = []
        for c in _COLS:
            v = getattr(p, c)
            vals.append(json.dumps(v) if c in ("metrics", "features") else v)
        with self.lock:
            self.conn.execute(
                f"INSERT INTO programs({','.join(_COLS)}) VALUES({','.join('?' * len(_COLS))})", vals)
            if p.ok and p.solution is not None:
                data = json.dumps(p.solution, separators=(",", ":"))
                if len(data) <= MAX_SOLUTION_CHARS:
                    self.conn.execute("INSERT OR REPLACE INTO solutions VALUES(?,?)", (p.id, data))
            return self._place(p, p.island) if p.ok else False

    def _place(self, p: Program, island: int) -> bool:
        rows = self.conn.execute(
            "SELECT p.score FROM elites e JOIN programs p ON p.id=e.program_id "
            "WHERE e.island=? AND e.cell=?", (island, p.cell)).fetchall()
        if rows and rows[0][0] is not None and rows[0][0] >= p.score:
            return False
        self.conn.execute("INSERT OR REPLACE INTO elites VALUES(?,?,?)", (island, p.cell, p.id))
        return True

    def solution(self, pid: str) -> dict | None:
        """The drawable solution the evaluator reported for a program, if any."""
        rows = self._q("SELECT data FROM solutions WHERE program_id=?", (pid,))
        return json.loads(rows[0][0]) if rows else None

    def seed_all_islands(self, p: Program) -> None:
        with self.lock:
            for i in range(self.islands):
                self._place(p, i)

    def migrate(self) -> list[tuple[int, int, str]]:
        """Ring migration: each island's best program joins the next island."""
        moved = []
        with self.lock:
            bests = [self.island_best(i) for i in range(self.islands)]
            for i, b in enumerate(bests):
                if b is None:
                    continue
                j = (i + 1) % self.islands
                if self._place(b, j):
                    moved.append((i, j, b.id))
        return moved

    # ------------------------------------------------------------- queries
    def best(self, island: int | None = None) -> Program | None:
        if island is not None:
            return self.island_best(island)
        rows = self._q("SELECT * FROM programs WHERE status='ok' ORDER BY score DESC, created LIMIT 1")
        return _row_to_program(rows[0]) if rows else None

    def island_elites(self, island: int) -> list[Program]:
        rows = self._q(
            "SELECT p.* FROM elites e JOIN programs p ON p.id=e.program_id WHERE e.island=? "
            "ORDER BY p.score DESC", (island,))
        return [_row_to_program(r) for r in rows]

    def island_best(self, island: int) -> Program | None:
        el = self.island_elites(island)
        return el[0] if el else None

    def island_top(self, island: int, k: int) -> list[Program]:
        """Strongest programs of an island: its own successes plus migrated elites."""
        rows = self._q(
            "SELECT * FROM programs WHERE status='ok' AND (island=? OR id IN "
            "(SELECT program_id FROM elites WHERE island=?)) ORDER BY score DESC LIMIT ?",
            (island, island, k))
        return [_row_to_program(r) for r in rows]

    def top(self, k: int, distinct_ideas: bool = True) -> list[Program]:
        rows = self._q("SELECT * FROM programs WHERE status='ok' ORDER BY score DESC LIMIT ?", (k * 4,))
        out, seen = [], set()
        for r in rows:
            p = _row_to_program(r)
            key = (p.idea or p.id).lower()[:80]
            if distinct_ideas and key in seen:
                continue
            seen.add(key)
            out.append(p)
            if len(out) == k:
                break
        return out

    def lineage(self, pid: str, limit: int = 50) -> list[Program]:
        """Ancestors of ``pid`` from the oldest shown to ``pid`` itself."""
        chain = []
        cur = self.get(pid)
        while cur is not None and len(chain) < limit:
            chain.append(cur)
            cur = self.get(cur.parent_id) if cur.parent_id else None
        return chain[::-1]

    def children(self, pid: str) -> list[Program]:
        rows = self._q("SELECT * FROM programs WHERE parent_id=? AND status<>'swept' "
                       "ORDER BY created", (pid,))
        return [_row_to_program(r) for r in rows]

    def recent(self, island: int | None, k: int, exclude_parent: str | None = None) -> list[Program]:
        sql, args = "SELECT * FROM programs WHERE status<>'swept'", []
        if island is not None:
            sql += " AND island=?"
            args.append(island)
        if exclude_parent:
            sql += " AND (parent_id IS NULL OR parent_id<>?)"
            args.append(exclude_parent)
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(k)
        return [_row_to_program(r) for r in self._q(sql, args)]

    def recent_failures(self, island: int, k: int) -> list[Program]:
        rows = self._q(
            "SELECT * FROM programs WHERE island=? AND status IN "
            "('compile_error','runtime_error','invalid') ORDER BY created DESC LIMIT ?",
            (island, k))
        return [_row_to_program(r) for r in rows]

    def all_programs(self) -> list[Program]:
        return [_row_to_program(r) for r in self._q("SELECT * FROM programs ORDER BY created")]

    # ----------------------------------------------------------- llm calls
    def add_call(self, program_id: str, st) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO llm_calls(program_id, created, model, total_seconds, load_seconds, "
                "prompt_tokens, prompt_seconds, gen_tokens, gen_seconds, stopped, cost_usd, "
                "provider) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (program_id, time.time(), st.model, st.total_seconds, st.load_seconds,
                 st.prompt_tokens, st.prompt_seconds, st.gen_tokens, st.gen_seconds,
                 int(st.stopped), getattr(st, "cost_usd", 0.0), getattr(st, "provider", "")))

    def calls(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM llm_calls ORDER BY created")]

    # --------------------------------------------------------------- notes
    def add_note(self, iteration: int, text: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO notes(created, iteration, text) VALUES(?,?,?)",
                              (time.time(), iteration, text))

    def latest_note(self) -> str:
        rows = self._q("SELECT text FROM notes ORDER BY id DESC LIMIT 1")
        return rows[0][0] if rows else ""

    # ----------------------------------------------------------- selection
    def sample_parent(self, island: int, rng: random.Random, exploit: float, explore: float,
                      repair: float) -> tuple[Program, str]:
        """Pick the program to improve next, and the selection kind used."""
        r = rng.random()
        if r < repair:
            # Repair a failure whose parent worked, once; never a repair of a repair.
            fails = [f for f in self.recent_failures(island, 6)
                     if not f.mode.startswith(("repair", "retry")) and not self.children(f.id)]
            if fails:
                return rng.choice(fails), "repair"
            r = rng.random() * (exploit + explore)
        else:
            r -= repair
        if r < exploit:
            top = self.island_top(island, 8)
            if top:
                # Rank-weighted: the best is most likely, but not certain.
                w = [1.0 / (i + 1) for i in range(len(top))]
                return rng.choices(top, weights=w)[0], "exploit"
        elif r < exploit + explore:
            el = self.island_elites(island)
            if el:
                return rng.choice(el), "explore"
        b = self.best()
        if b is None:
            raise RuntimeError("database has no successful program to start from")
        return b, "best"

    def sample_inspirations(self, parent: Program, island: int, k: int,
                            rng: random.Random) -> list[Program]:
        if k <= 0:
            return []
        picked: dict[str, Program] = {}
        pool = [p for p in self.island_elites(island) if p.id != parent.id
                and p.code_hash != parent.code_hash]
        if pool:
            picked[pool[0].id] = pool[0]  # the island's best other niche
        others = [b for i in range(self.islands) if i != island
                  for b in [self.island_best(i)] if b is not None]
        cand = pool[1:] + others
        rng.shuffle(cand)
        for p in cand:
            if len(picked) >= k:
                break
            if p.id != parent.id and p.code_hash != parent.code_hash and \
                    all(q.code_hash != p.code_hash for q in picked.values()):
                picked[p.id] = p
        return list(picked.values())[:k]

# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""The search loop: sample, prompt, edit, evaluate, record, repeated in parallel.

Each worker thread runs whole iterations. Two semaphores bound the expensive
resources separately: ``llm.concurrency`` requests to the model server and
``evaluator.concurrency`` evaluation jobs. With more workers than LLM slots,
the model generates the next candidates while earlier ones are compiled and
benchmarked: an asynchronous pipeline at the scale of a single machine.

Island ``i`` receives iterations ``i, i+N, i+2N, ...``, so every branch of
the search progresses at the same rate.
"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .blocks import (EditError, apply_answer, editable_text, parse_response, strip_thinking,
                     unified_diff)
from .config import ANSWER_ROOM, ProblemConfig
from .database import Database, Program, code_hash, new_id
from .evaluator import Evaluator
from .llm import LLM, Ensemble, LLMError
from .policy import Policy
from .prompt import (Context, build_insights_messages, build_messages, fmt_score, journal_line,
                     recurring_errors)
from .sweep import Sweep, fmt_point, parse_sweep, substitute


# Failures worth an immediate fix-up retry: the idea may be fine, the code is buggy.
FIXABLE = ("compile_error", "runtime_error", "invalid")


class _AllPermits:
    """Holds every permit of a semaphore: no LLM request runs while held.
    Exclusive stages never overlap each other, so taking permits one by one is safe."""

    def __init__(self, sem: threading.Semaphore, n: int):
        self.sem, self.n = sem, n

    def acquire(self) -> None:
        for _ in range(self.n):
            self.sem.acquire()

    def release(self) -> None:
        for _ in range(self.n):
            self.sem.release()


@dataclass
class RunStats:
    started: float
    iterations_done: int = 0
    improvements: int = 0


class Engine:
    def __init__(self, cfg: ProblemConfig, run_dir: Path, llm: LLM | None = None,
                 log: Callable[[str], None] = print):
        self.cfg = cfg
        self.s = cfg.search
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "llm").mkdir(exist_ok=True)
        self.db = Database(self.run_dir / "omega.sqlite", self.s.islands, self.s.features)
        self.db.set_meta("islands", str(self.s.islands))
        self.db.set_meta("config_dir", str(cfg.problem_dir))
        self.llm = llm or Ensemble(cfg.llm)
        self.log = log
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._llm_sem = threading.Semaphore(max(1, cfg.llm.concurrency))
        self._eval_sem = threading.Semaphore(max(1, cfg.evaluator.concurrency))
        self.evaluator = Evaluator(cfg, self.run_dir / "jobs",
                                   quiet=_AllPermits(self._llm_sem, max(1, cfg.llm.concurrency)))
        self._insights_busy = threading.Lock()
        self._sweep_pool = ThreadPoolExecutor(max_workers=max(1, cfg.evaluator.concurrency),
                                              thread_name_prefix="omega-sweep")
        self._initial_size = 1
        self.stats = RunStats(started=time.time())
        self._deadline: float | None = None
        self._llm_failures = 0
        self._since_progress = 0  # answers since the last new best that counted as progress
        #: why the last run ended: "time limit", "no progress", "iterations", "target score",
        #: "cost limit", "model errors" or "interrupted"
        self.stop_reason = ""
        s = self.s
        tune = s.tune_ratio if s.sweep_budget > 0 else 0.0
        self.policy = Policy(
            {"refine": max(0.0, 1 - s.bold_ratio - tune), "bold": s.bold_ratio, "tune": tune},
            {"diff": max(0.0, 1 - s.rewrite_ratio), "rewrite": s.rewrite_ratio},
            adaptive=s.adaptive)

    # ------------------------------------------------------------ helpers
    def _features(self, code: str, evaluated: dict) -> dict:
        f = dict(evaluated)
        f["code_size"] = max(1, len(editable_text(code))) / self._initial_size
        return f

    def _llm_log(self, pid: str, messages: list[dict], response: str, model: str) -> None:
        parts = [f"# {pid} via {model}\n"]
        for m in messages:
            parts.append(f"\n---- {m['role']} ----\n{m['content']}\n")
        parts.append(f"\n---- response ----\n{response}\n")
        (self.run_dir / "llm" / f"{pid}.md").write_text("".join(parts), encoding="utf-8",
                                                        newline="\n")

    # ------------------------------------------------------------- set-up
    def initialize(self) -> Program:
        """Evaluate and store the initial program (once per run directory)."""
        code = self.cfg.initial_path.read_text(encoding="utf-8")
        self._initial_size = max(1, len(editable_text(code)))
        root_id = self.db.meta("root_id")
        if root_id:
            root = self.db.get(root_id)
            assert root is not None
            self._replay_policy()
            return root
        self.log(f"evaluating initial program {self.cfg.initial} ...")
        res = self.evaluator.evaluate(code, "initial")
        p = Program(id=new_id(), code=code, idea="initial program", mode="initial",
                    status=res.status, score=res.score,
                    metrics={**res.metrics, **res.usage_metrics()},
                    features=self._features(code, res.features), feedback=res.feedback,
                    stage=res.stage, eval_seconds=res.seconds, iteration=-1,
                    solution=res.solution)
        self._confirm(p, force=True)  # a noisy baseline would skew every comparison
        if not p.ok:
            raise RuntimeError(
                f"the initial program does not pass the evaluator ({p.status}):\n{p.feedback}")
        self.db.add(p)
        self.db.seed_all_islands(p)
        self.db.set_meta("root_id", p.id)
        self.db.set_meta("problem", self.cfg.name)
        self.log(f"initial score {fmt_score(p.score)}  ({res.seconds:.1f}s)  {self._metrics(p)}")
        return p

    def _answer_budget(self, parent: Program) -> int:
        """Character budget of one answer: a full rewrite of the program plus room
        for edits; anything much longer is a model talking to itself."""
        if self.cfg.llm.max_answer_chars:
            return self.cfg.llm.max_answer_chars
        return 2 * len(parent.code) + ANSWER_ROOM

    def _replay_policy(self) -> None:
        """Rebuild the strategy statistics from a resumed run's history."""
        progs = self.db.all_programs()
        by_id = {p.id: p for p in progs}
        swept_up = {p.parent_id for p in progs if p.mode.startswith("sweep/") and p.ok}
        for p in progs:
            par = by_id.get(p.parent_id or "")
            self.policy.observe(p, par.score if par is not None and par.ok else None,
                                p.id in swept_up)

    @staticmethod
    def _metrics(p: Program) -> str:
        return " ".join(f"{k}={fmt_score(v) if isinstance(v, float) else v}"
                        for k, v in list(p.metrics.items())[:6])

    # ----------------------------------------------------------- one step
    def build_context(self, island: int, rng: random.Random) -> Context:
        db, s = self.db, self.s
        parent, selection = db.sample_parent(island, rng, s.exploit_ratio, s.explore_ratio,
                                             s.repair_ratio)
        style = "fix" if selection == "repair" else self.policy.style(rng)
        edit = self.policy.edit(rng)
        lineage = db.lineage(parent.id, limit=10)
        children = db.children(parent.id)[-10:]
        recent = db.recent(island, s.journal_lines, exclude_parent=parent.id)
        lineage_ids = {p.id for p in lineage}
        recent = [p for p in recent if p.id not in lineage_ids][::-1]
        scores: dict[str, float | None] = {}
        for p in recent:
            if p.parent_id and p.parent_id not in scores:
                par = db.get(p.parent_id)
                scores[p.parent_id] = par.score if par and par.ok else None
        window = db.recent(island, 20)
        return Context(
            parent=parent, selection=selection, edit=edit, style=style, island=island,
            lineage=lineage, children=children, recent=recent,
            errors=recurring_errors(window),
            leaders=db.top(5), lineage_scores=scores, notes=db.latest_note(),
            inspirations=db.sample_inspirations(parent, island, s.num_inspirations, rng))

    def fix_context(self, failed: Program, rng: random.Random) -> Context:
        """Context for an immediate retry: the failed program becomes the parent."""
        db = self.db
        return Context(
            parent=failed, selection="retry", edit="diff", style="fix", island=failed.island,
            lineage=db.lineage(failed.id, limit=6), children=[], recent=[],
            leaders=[], notes=db.latest_note(), inspirations=[])

    def step(self, it: int) -> Program | None:
        rng = random.Random(self.s.seed * 1_000_003 + it)
        island = it % self.s.islands
        result = self._attempt(it, island, rng)
        for _ in range(self.s.fix_retries):
            if result is None or result.status not in FIXABLE or self._expired():
                break
            result = self._attempt(it, island, rng, fix=result) or result
        return result

    def _halt(self, reason: str) -> None:
        """End the run; the first reason given is the one recorded."""
        if not self.stop_reason:
            self.stop_reason = reason
        self._stop.set()

    def _expired(self) -> bool:
        if self._stop.is_set():
            return True
        if self._deadline is not None and time.monotonic() > self._deadline:
            self._halt("time limit")
            return True
        return False

    def _progress(self, child: Program, best: Program | None) -> bool:
        """Whether ``child`` beats ``best`` by enough to reset the patience count."""
        if not child.ok or child.score is None:
            return False
        if best is None or best.score is None:
            return True
        return child.score - best.score >= self.s.min_gain * max(abs(best.score), 1e-12)

    def _attempt(self, it: int, island: int, rng: random.Random,
                 fix: Program | None = None) -> Program | None:
        """One LLM answer, evaluated and recorded; None if the run ended while queued."""
        pid = new_id()
        # The context is built only once an LLM slot is free, so the prompt
        # reflects every result that finished while this worker was waiting.
        with self._llm_sem:
            if self._expired():
                return None
            ctx = self.build_context(island, rng) if fix is None else self.fix_context(fix, rng)
            parent = ctx.parent
            # The prompt gets what the answer leaves of the context window.
            room = max(self.cfg.llm.num_ctx - self.cfg.llm.max_tokens, self.cfg.llm.num_ctx // 4)
            messages = build_messages(self.cfg, ctx, char_budget=int(room * 2.8))
            child = Program(id=pid, code=parent.code, parent_id=parent.id, island=island,
                            generation=parent.generation + 1, iteration=it,
                            mode=f"{ctx.selection}/{ctx.edit}/{ctx.style}")
            t0 = time.monotonic()
            try:
                text, model = self.llm.complete(messages, rng, max_chars=self._answer_budget(parent))
            except LLMError as e:
                text, model = None, ""
                child.status, child.feedback = "llm_error", str(e)[:500]
                if e.partial:
                    child.idea = parse_response(e.partial).idea
                    self._llm_log(pid, messages, e.partial + "\n[stopped: " + str(e) + "]", "")
            child.llm_seconds = time.monotonic() - t0
            st = getattr(self.llm, "last_stats", None)
            if st is not None:
                self.db.add_call(pid, st)
        if text is None:
            return self._record(child, parent)  # outside the slot: may update the notebook
        child.model = model
        self._llm_log(pid, messages, text, model)

        parsed = parse_response(text)
        child.idea = parsed.idea
        params, sweep_error = [], ""
        if self.s.sweep_budget > 0:
            try:
                params = parse_sweep(strip_thinking(text))
            except EditError as e:
                sweep_error = str(e)
        try:
            new_code = apply_answer(parent.code, parsed)
        except EditError as e:
            child.status, child.feedback = "edit_error", str(e)
            return self._record(child, parent)
        child.code = new_code
        child.code_hash = code_hash(new_code)
        child.diff = unified_diff(parent.code, new_code, self.cfg.candidate_name)
        if not child.idea:
            child.idea = _guess_idea(child.diff)

        dup = self.db.by_hash(child.code_hash)
        if dup is not None:
            child.status = "duplicate"
            child.feedback = f"identical to #{dup.id[:6]} ({dup.status}, score {fmt_score(dup.score)})"
            return self._record(child, parent)

        with self._eval_sem:
            res = self.evaluator.evaluate(new_code, pid)
        child.status, child.score = res.status, res.score
        child.metrics = {**res.metrics, **res.usage_metrics()}
        child.feedback, child.stage, child.eval_seconds = res.feedback, res.stage, res.seconds
        child.features = self._features(new_code, res.features)
        child.solution = res.solution
        self._confirm(child)
        if sweep_error:
            child.feedback = (child.feedback + "\n" if child.feedback else "") + f"sweep rejected: {sweep_error}"
        if params and child.ok:
            return self._run_sweep(child, parent, params, rng)
        return self._record(child, parent)

    def _confirm(self, p: Program, force: bool = False) -> None:
        """Re-evaluate an apparent new best and keep the median score.

        With noisy evaluators (timing benchmarks) a single lucky run would
        otherwise become the best program permanently.
        """
        n = self.cfg.evaluator.confirm_runs
        if n <= 0 or not p.ok:
            return
        best = self.db.best()
        if not force and best is not None and p.score <= best.score:
            return
        scores = [p.score]
        for k in range(n):
            with self._eval_sem:
                res = self.evaluator.evaluate(p.code, f"{p.id}-c{k}")
            if res.status != "ok" or res.score is None:
                p.status = res.status
                p.feedback = f"failed on re-evaluation {k + 1}: {res.feedback}"
                p.score = None
                return
            scores.append(res.score)
            p.eval_seconds += res.seconds
        scores.sort()
        p.score = scores[len(scores) // 2]
        p.metrics = dict(p.metrics, score_samples=[round(s, 6) for s in scores])

    # -------------------------------------------------------------- sweeps
    def _eval_point(self, base: Program, point: dict) -> Program:
        p = Program(id=new_id(), code=base.code, parent_id=base.id, island=base.island,
                    generation=base.generation + 1, iteration=base.iteration,
                    mode=f"sweep/{base.mode}", model=base.model,
                    idea=f"sweep point {fmt_point(point)}")
        try:
            p.code = substitute(base.code, point, self.cfg.lang_tag)
        except EditError as e:
            p.status, p.feedback = "edit_error", str(e)
            return p
        p.code_hash = code_hash(p.code)
        dup = self.db.by_hash(p.code_hash)
        if dup is not None:
            p.status, p.score, p.metrics = dup.status, dup.score, dup.metrics
            p.features, p.feedback = dup.features, dup.feedback
            p.id = dup.id  # already stored; never stored twice
            return p
        with self._eval_sem:
            res = self.evaluator.evaluate(p.code, p.id)
        p.status, p.score = res.status, res.score
        p.metrics = {**res.metrics, **res.usage_metrics()}
        p.feedback, p.stage, p.eval_seconds = res.feedback, res.stage, res.seconds
        p.features = self._features(p.code, res.features)
        p.solution = res.solution
        p.diff = unified_diff(base.code, p.code, self.cfg.candidate_name)
        return p

    def _run_sweep(self, base: Program, parent: Program, params, rng: random.Random) -> Program:
        """Evaluate engine-chosen variants of ``base``; keep the best as one child."""
        # A first substitution with the base values themselves validates the names.
        try:
            substitute(base.code, {p.name: p.sample(rng) for p in params}, self.cfg.lang_tag)
        except EditError as e:
            base.feedback = (base.feedback + "\n" if base.feedback else "") + f"sweep rejected: {e}"
            return self._record(base, parent)
        sweep = Sweep(params, self.s.sweep_budget, rng)
        evaluated: list[Program] = []
        points: dict[str, dict] = {}
        width = max(1, self.cfg.evaluator.concurrency)
        t0 = time.monotonic()
        while sweep.remaining() > 0 and not self._stop.is_set():
            batch = sweep.next_batch(width)
            if not batch:
                break
            progs = list(self._sweep_pool.map(lambda pt: self._eval_point(base, pt), batch))
            for pt, prog in zip(batch, progs):
                sweep.report(pt, prog.score if prog.ok else None)
                evaluated.append(prog)
                points[prog.id] = pt
        summary = sweep.summary(base.score)
        base.feedback = (base.feedback + "\n" if base.feedback else "") + summary
        winner = None
        new = [p for p in evaluated if self.db.get(p.id) is None]
        ok = [p for p in new if p.ok]
        if ok:
            top = max(ok, key=lambda p: p.score)
            # The maximum of noisy samples is biased upward: confirm before accepting.
            self._confirm(top, force=True)
            if top.ok and top.score > base.score:
                winner = top
        for p in new:
            if p is not winner:
                if not p.ok:
                    p.feedback = f"{p.status}: {p.feedback}"
                p.status = "swept"
                self.db.add(p)
        self.log(f"  sweep #{base.id[:6]}: {len(evaluated)} variants in "
                 f"{time.monotonic() - t0:.0f}s")
        result = self._record(base, parent, sweep_won=winner is not None)
        if winner is not None:
            winner.idea = _one_line_idea(
                f"tuned {fmt_point(points[winner.id])} on top of: {base.idea}")
            winner.feedback = summary
            winner.mode = f"sweep/{base.mode}"
            result = self._record(winner, base)
        return result

    def _record(self, child: Program, parent: Program, sweep_won: bool = False) -> Program:
        self.policy.observe(child, parent.score if parent.ok else None, sweep_won)
        with self._lock:
            self._llm_failures = self._llm_failures + 1 if child.status == "llm_error" \
                and "answer stopped" not in child.feedback else 0
            budget = self.cfg.llm.max_cost_usd
            spent = getattr(self.llm, "cost_usd", 0.0)
            if budget and spent >= budget and not self._stop.is_set():
                self.log(f"stopping: model requests cost ${spent:.2f}, budget ${budget:.2f}")
                self._halt("cost limit")
            limit = self.cfg.llm.max_consecutive_errors
            if limit and self._llm_failures >= limit and not self._stop.is_set():
                self.log(f"stopping: {self._llm_failures} model requests failed in a row "
                         f"({child.feedback[:120]})")
                self._halt("model errors")
            best_before = self.db.best()
            self._since_progress = 0 if self._progress(child, best_before) \
                else self._since_progress + 1
            if self.s.patience and self._since_progress >= self.s.patience \
                    and not self._stop.is_set():
                self.log(f"stopping: {self._since_progress} answers in a row without a new "
                         f"best that is at least {self.s.min_gain:.1%} better")
                self._halt("no progress")
            self.db.add(child)
            done = self.stats.iterations_done = self.stats.iterations_done + 1
            new_best = child.ok and best_before is not None and child.score > best_before.score
            if new_best:
                self.stats.improvements += 1
            migrate = self.s.migration_interval and done % self.s.migration_interval == 0
            insights = self.s.insights_interval and done % self.s.insights_interval == 0
        mark = "★" if new_best else (" " if child.ok else "✗")
        ps = parent.score if parent.ok else None
        self.log(f"{mark} it {child.iteration:4d} i{child.island} "
                 f"{journal_line(child, ps, idea_len=90)}"
                 f"  [{child.llm_seconds:.0f}s llm, {child.eval_seconds:.1f}s eval]")
        if new_best:
            self.db.set_meta("best_id", child.id)
            (self.run_dir / f"best_{self.cfg.candidate_name}").write_text(
                child.code, encoding="utf-8", newline="\n")
        if migrate and self.s.islands > 1:
            for i, j, pid in self.db.migrate():
                self.log(f"  migrate #{pid[:6]} island {i} -> {j}")
        if insights:
            self._update_insights()
        t = self.s.target_score
        if t is not None and child.ok and child.score >= t:
            self.log(f"target score {t} reached")
            self._halt("target score")
        return child

    def _update_insights(self) -> None:
        if not self._insights_busy.acquire(blocking=False):
            return  # an update is already running
        try:
            recent = self.db.recent(None, 40)[::-1]
            lines = []
            for p in recent:
                par = self.db.get(p.parent_id) if p.parent_id else None
                lines.append(journal_line(p, par.score if par and par.ok else None))
            best = self.db.best()
            msgs = build_insights_messages(self.cfg, self.db.latest_note(), lines,
                                           best.score if best else None)
            with self._llm_sem:
                text, _ = self.llm.complete(msgs, random.Random(),
                                            model=self.cfg.llm.insights_model)
            text = strip_thinking(text).strip()
            if text:
                self.db.add_note(self.stats.iterations_done, text)
                (self.run_dir / "notebook.md").write_text(text + "\n", encoding="utf-8",
                                                          newline="\n")
                self.log(f"  notebook updated; strategy wins/tries: {self.policy.summary()}")
        except LLMError as e:
            self.log(f"  notebook update failed: {e}")
        finally:
            self._insights_busy.release()

    # ---------------------------------------------------------------- run
    def run(self, iterations: int | None = None) -> Program | None:
        self.initialize()
        total = iterations if iterations is not None else self.s.iterations
        start_it = self.db.max_iteration() + 1
        end_it = start_it + total
        self.stats = RunStats(started=time.time())
        self._deadline = time.monotonic() + self.s.max_minutes * 60 if self.s.max_minutes else None
        self._since_progress = 0
        self.stop_reason = ""
        self._stop.clear()
        counter = iter(range(start_it, end_it))
        counter_lock = threading.Lock()

        def next_it() -> int | None:
            if self._expired():
                return None
            with counter_lock:
                return next(counter, None)

        def worker() -> None:
            while (it := next_it()) is not None:
                try:
                    self.step(it)
                except Exception as e:  # noqa: BLE001 - keep the search alive
                    self.log(f"! iteration {it} crashed: {type(e).__name__}: {e}")

        from .llm import remote_models
        remote = remote_models(self.cfg.llm)
        if remote:
            self.log(f"note: prompts, including the program code, are sent to a cloud service "
                     f"for {', '.join(remote)}")
        workers = max(1, self.cfg.llm.concurrency + self.cfg.evaluator.concurrency)
        self.log(f"run {self.run_dir}  iterations {start_it}..{end_it - 1}  islands "
                 f"{self.s.islands}  workers {workers} (llm {self.cfg.llm.concurrency}, "
                 f"eval {self.cfg.evaluator.concurrency})")
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="omega")
        futures = [pool.submit(worker) for _ in range(workers)]
        try:
            while not all(f.done() for f in futures):
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.log("stopping: waiting for in-flight iterations (Ctrl-C again to abort)")
            self._halt("interrupted")
            try:
                pool.shutdown(wait=True)
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
        pool.shutdown(wait=True)
        if not self.stop_reason:
            self.stop_reason = "iterations"
        best = self.db.best()
        self.log(f"done ({self.stop_reason}): {self.stats.iterations_done} iterations, "
                 f"{self.stats.improvements} new bests, best #{best.id[:6] if best else '-'} score "
                 f"{fmt_score(best.score if best else None)}")
        return best

    def stop(self) -> None:
        self._halt("interrupted")

    def close(self) -> None:
        """Close the run database; a benchmark creates one engine per run."""
        self.db.close()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _one_line_idea(text: str) -> str:
    return " ".join(text.split())[:300]


def _guess_idea(diff: str) -> str:
    added = [ln[1:].strip() for ln in diff.splitlines()
             if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()]
    return ("edit: " + " / ".join(added[:2]))[:140] if added else "edit"


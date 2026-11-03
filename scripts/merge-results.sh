#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Merge the benchmark results of another machine into this repository: meant to
# run unattended once a day (see "Collecting results automatically" in
# docs/benchmarking.md and scripts/install-merge-job.sh), but safe to run by hand.
#
#   1. mirror the other machine's result folder into bench/results (results
#      deleted there are deleted here too),
#   2. date new models (`omega model-releases`) and take an OpenRouter snapshot,
#   3. rebuild the report and the docs and check them (`omega docs --check`,
#      `ckdocs check`),
#   4. commit only those files and, if configured, publish.
#
# Settings come from the environment or from ~/.config/omega-merge.env (another
# file with OMEGA_MERGE_CONFIG), a shell file of variable assignments:
#   OMEGA_MERGE_HOST      ssh host that runs the benchmark (required)
#   OMEGA_MERGE_RESULTS   its result folder, as scp writes it (required),
#                         e.g. V:/omega/results on Windows
#   OMEGA_MERGE_STATUS    a command run there whose output is logged (optional)
#   OMEGA_MERGE_PUBLISH   a command run here after the commit (optional)
#   OMEGA_MERGE_SNAPSHOT  0 to skip the OpenRouter snapshot (default 1)
#   OMEGA_MERGE_SCRATCH   folder for the copy (default $TMPDIR)
#
# It touches nothing when the files it owns (bench/results, bench/openrouter,
# bench/model-releases.json, docs/) already have uncommitted changes: they may be
# someone's work in progress. Every step is logged; a failure leaves the changes
# in place for inspection, and the next run refuses to start until they are gone.
set -euo pipefail

config=${OMEGA_MERGE_CONFIG:-$HOME/.config/omega-merge.env}
# shellcheck disable=SC1090
[ -f "$config" ] && . "$config"
HOST=${OMEGA_MERGE_HOST:-}
RESULTS=${OMEGA_MERGE_RESULTS:-}
SCRATCH_ROOT=${OMEGA_MERGE_SCRATCH:-${TMPDIR:-/tmp}}
OWNED=(bench/results bench/openrouter bench/model-releases.json docs)

cd "$(dirname "$0")/.."
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
die() { log "FAILED: $*"; exit 1; }
[ -n "$HOST" ] && [ -n "$RESULTS" ] || die "set OMEGA_MERGE_HOST and OMEGA_MERGE_RESULTS (in $config)"

mkdir -p "$SCRATCH_ROOT"
lock="$SCRATCH_ROOT/omega-merge.lock"
mkdir "$lock" 2>/dev/null || die "another merge is running ($lock)"
trap 'rmdir "$lock"' EXIT

[ "$(git branch --show-current)" = main ] || die "not on main"
dirty=$(git status --porcelain -- "${OWNED[@]}")
[ -z "$dirty" ] || die "uncommitted changes in the merged files, not touching them:
$dirty"

# `echo` exists on every system, `true` not on Windows.
err=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$HOST" echo ok 2>&1) \
    || die "$HOST is not reachable: $err"
if [ -n "${OMEGA_MERGE_STATUS:-}" ]; then
    status=$(ssh -o BatchMode=yes "$HOST" "$OMEGA_MERGE_STATUS" 2>&1 | tr -d '\r') || true
    log "$HOST: $(echo "$status" | tr '\n' ';')"
fi

scratch="$SCRATCH_ROOT/merge-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$scratch"
trap 'rm -rf "$scratch"; rmdir "$lock"' EXIT
scp -q -o BatchMode=yes "$HOST:$RESULTS/*.json" "$scratch/" || die "copying the results failed"
count=$(find "$scratch" -name '*.json' | wc -l | tr -d ' ')
[ "$count" -gt 0 ] || die "$HOST returned no results"
rsync -a --delete "$scratch/" bench/results/
log "mirrored $count results"

missing=$(python3 - <<'EOF'
import glob, json
known = set(json.load(open("bench/model-releases.json"))["models"])
print(" ".join(sorted({json.load(open(f))["model"] for f in glob.glob("bench/results/*.json")} - known)))
EOF
)
if [ -n "$missing" ]; then
    log "dating new models: $missing"
    python3 -m omega_transform model-releases || log "model-releases failed; continuing"
fi
if [ "${OMEGA_MERGE_SNAPSHOT:-1}" != 0 ]; then
    python3 -m omega_transform openrouter-snapshot || log "OpenRouter snapshot failed; continuing"
fi

python3 -m omega_transform report >/dev/null || die "omega report"
python3 -m omega_transform docs >/dev/null || die "omega docs"
python3 -m omega_transform docs --check || die "omega docs --check"
ckdocs check --quiet || die "ckdocs check"

git add -A -- "${OWNED[@]}"
if git diff --cached --quiet -- "${OWNED[@]}"; then
    log "nothing changed"
    exit 0
fi
summary=$(python3 - <<'EOF'
import collections, glob, json
c = collections.Counter()
for f in glob.glob("bench/results/*.json"):
    d = json.load(open(f))
    c[("cloud" if d.get("provider") == "openrouter" else "local", d.get("variant"))] += 1
print(", ".join(f"{n} {where} {variant}" for (where, variant), n in sorted(c.items())))
EOF
)
git commit -q -m "Benchmark results from $HOST up to $(date +%Y-%m-%d)

$count runs: $summary. Merged automatically by scripts/merge-results.sh." \
    -- "${OWNED[@]}" || die "commit"
log "committed $(git log --oneline -1)"
if [ -n "${OMEGA_MERGE_PUBLISH:-}" ]; then
    bash -c "$OMEGA_MERGE_PUBLISH" || die "publishing ($OMEGA_MERGE_PUBLISH)"
    log "published"
fi

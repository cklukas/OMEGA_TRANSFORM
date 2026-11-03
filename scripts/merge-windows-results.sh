#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Merge the benchmark results of the Windows workstation into this repository
# and publish them: meant to run unattended once a day (launchd, see
# scripts/com.klukas.omega-merge.plist), but safe to run by hand.
#
#   1. mirror V:\omega_transform\bench-v4\results into bench/results (results
#      deleted on Windows are deleted here too),
#   2. date new models (`omega model-releases`) and take an OpenRouter snapshot,
#   3. rebuild the report and the docs and check them (`omega docs --check`,
#      `ckdocs check`): about a minute of one core, so it does not wait for a
#      low load as builds, tests and benchmarks on this Mac do,
#   4. commit only those files and publish to rpi4 and GitHub.
#
# It touches nothing when the files it owns (bench/results, bench/openrouter,
# bench/model-releases.json, docs/) already have uncommitted changes: they may be
# another session's work. Every step is logged; a failure leaves the changes in
# place for inspection, and the next run refuses to start until they are gone.
set -euo pipefail

WS=${OMEGA_WORKSTATION:-workstation}
REMOTE_RESULTS=${OMEGA_REMOTE_RESULTS:-V:/omega_transform/bench-v4/results}
REMOTE_LOGS=${OMEGA_REMOTE_LOGS:-'V:\omega_transform\bench-v4'}
SCRATCH_ROOT=${OMEGA_SCRATCH:-/Volumes/S8T/tmp/OMEGA_TRANSFORM}
OWNED=(bench/results bench/openrouter bench/model-releases.json docs)

cd "$(dirname "$0")/.."
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
die() { log "FAILED: $*"; exit 1; }

lock="${TMPDIR:-/tmp}/omega-merge.lock"
mkdir "$lock" 2>/dev/null || die "another merge is running ($lock)"
trap 'rmdir "$lock"' EXIT

[ "$(git branch --show-current)" = main ] || die "not on main"
dirty=$(git status --porcelain -- "${OWNED[@]}")
[ -z "$dirty" ] || die "uncommitted changes in the merged files, not touching them:
$dirty"

err=$(ssh -o BatchMode=yes -o ConnectTimeout=30 "$WS" echo ok 2>&1) \
    || die "workstation $WS is not reachable: $err"
status=$(ssh -o BatchMode=yes "$WS" "powershell -NoProfile -Command \"Get-ScheduledTask OmegaBenchV4,OmegaBenchV4Cloud | ForEach-Object { [Console]::WriteLine(\$_.TaskName + ' ' + \$_.State) }; foreach (\$l in 'bench-v4.log','bench-v4-cloud.log') { [Console]::WriteLine(\$l + ' done: ' + [bool](Select-String -Path ('$REMOTE_LOGS\\' + \$l) -Pattern '##### done' -SimpleMatch -Quiet)) }\"" | tr -d '\r')
log "workstation: $(echo "$status" | tr '\n' ';')"

scratch="$SCRATCH_ROOT/merge-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$scratch"
trap 'rm -rf "$scratch"; rmdir "$lock"' EXIT
scp -q -o BatchMode=yes "$WS:$REMOTE_RESULTS/*.json" "$scratch/" || die "copying the results failed"
count=$(find "$scratch" -name '*.json' | wc -l | tr -d ' ')
[ "$count" -gt 0 ] || die "the workstation returned no results"
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
python3 -m omega_transform openrouter-snapshot || log "OpenRouter snapshot failed; continuing"

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
git commit -q -m "Benchmark v4: Windows results up to $(date +%Y-%m-%d)

$count runs: $summary. Merged automatically by scripts/merge-windows-results.sh." \
    -- "${OWNED[@]}" || die "commit"
log "committed $(git log --oneline -1)"
ckgit publish --branch main --no-tags --yes || die "publishing to rpi4"
scripts/publish-github.sh || die "publishing to GitHub"
log "published"

#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Run the model benchmark. First `omega models` quickly checks every installed
# Ollama model (does it load, answer, follow the edit format, and how fast is
# it?) and writes bench/model-check.json. Then every model marked ok runs on
# every example, once without and once with its reasoning mode ("thinking";
# models without one are skipped in that pass). Finally docs/benchmarks.md (with
# its sub-pages in docs/benchmarks/) and the example pages (docs/examples/, with each example's best result) are
# regenerated.
#
# Rerun it after changing the engine, the prompts or the examples, or after
# pulling new models. Every run adds result files to bench/results/; commit
# them together with the regenerated pages to publish the tables and charts.
#
# Usage:
#   scripts/run-benchmarks.sh [extra omega bench options]
#
# Environment:
#   MINUTES   search budget per run (default 15)
#   MODES     reasoning modes to run, in order (default "no-think think")
#   CHECK     0 to reuse the existing bench/model-check.json (default 1: recheck)
#   WAIT_LOAD start only once the 1-minute load average is below this (default: start now)
#   WAIT_RAM_GB start only once at least this much RAM is free, measured like the
#             memory guard does (default: start now)
#   WAIT_HOURS give up waiting after this long and do not run at all (default 12)
#   IGNORE_RAM 1 to load models whatever the free memory (the OS swaps); default 0
#   RESERVE_GB RAM kept free for the system, evaluation jobs and other applications
#             (default 6); a model is only loaded if it fits beside this reserve
#
# Models are never loaded when they would not fit in the memory free at that
# moment (see --reserve-gb); they are skipped and reported instead.
#
# Examples:
#   scripts/run-benchmarks.sh --dry-run
#   MINUTES=5 scripts/run-benchmarks.sh --models qwen3.5:latest gemma4:latest
#   MODES=think scripts/run-benchmarks.sh --examples examples/matmul_cpp
#
# The machine should be otherwise idle: the C++ example measures speed, and
# the time per model call includes waiting for a busy Ollama server.
set -euo pipefail

cd "$(dirname "$0")/.."
MINUTES="${MINUTES:-15}"
MODES="${MODES:-no-think think}"
CHECK="${CHECK:-1}"
WAIT_LOAD="${WAIT_LOAD:-}"
WAIT_RAM_GB="${WAIT_RAM_GB:-}"
WAIT_HOURS="${WAIT_HOURS:-12}"
if [ "${IGNORE_RAM:-0}" != 0 ]; then
  export OMEGA_NO_MEMORY_GUARD=1  # read by omega_transform.memory
fi
RESERVE_GB="${RESERVE_GB:-6}"
PYTHON="${PYTHON:-python3}"
mkdir -p logs
log="logs/bench-$(date +%Y%m%d-%H%M%S).log"

loadavg() { "$PYTHON" -c 'import os; print(f"{os.getloadavg()[0]:.1f}")'; }
free_ram() {
  "$PYTHON" -c 'from omega_transform.memory import available_gb as a; v = a(); print(f"{v or 0:.1f}")'
}
# True (exit 0) while a start condition is not met yet.
must_wait() {
  if [ -n "$WAIT_LOAD" ] && awk -v l="$(loadavg)" -v t="$WAIT_LOAD" 'BEGIN { exit !(l >= t) }'; then
    return 0
  fi
  if [ -n "$WAIT_RAM_GB" ] && awk -v f="$(free_ram)" -v t="$WAIT_RAM_GB" 'BEGIN { exit !(f < t) }'; then
    return 0
  fi
  return 1
}
if [ -n "$WAIT_LOAD$WAIT_RAM_GB" ]; then
  echo "waiting until ${WAIT_LOAD:+load average < $WAIT_LOAD}${WAIT_LOAD:+${WAIT_RAM_GB:+ and }}${WAIT_RAM_GB:+free RAM >= $WAIT_RAM_GB GB}" \
       "(now: load $(loadavg), free $(free_ram) GB; giving up after $WAIT_HOURS h) ..." | tee -a "$log"
  deadline=$(( $(date +%s) + $(awk -v h="$WAIT_HOURS" 'BEGIN { printf "%d", h * 3600 }') ))
  while must_wait; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "gave up after $WAIT_HOURS h (load $(loadavg), free $(free_ram) GB); nothing was run" \
        | tee -a "$log"
      exit 3
    fi
    sleep 30
  done
  echo "load $(loadavg), free RAM $(free_ram) GB: starting ($(date))" | tee -a "$log"
fi

# With WAIT_RAM_GB, memory may shrink again after the start: every model then also
# waits (up to WAIT_HOURS) until it fits, instead of being skipped.
wait_ram_minutes=0
if [ -n "$WAIT_RAM_GB" ]; then
  wait_ram_minutes=$(awk -v h="$WAIT_HOURS" 'BEGIN { printf "%d", h * 60 }')
fi

if [ -n "$(git status --porcelain -- omega_transform examples 2>/dev/null)" ]; then
  echo "warning: uncommitted changes in omega_transform/ or examples/;" \
       "the results will record the last commit, not this code" | tee -a "$log"
fi

# Keep the Mac awake for the whole benchmark (no-op elsewhere).
keep_awake=()
if command -v caffeinate >/dev/null 2>&1; then
  keep_awake=(caffeinate -i)
fi

if [ "$CHECK" != 0 ]; then
  echo "##### model check  ($(date))" | tee -a "$log"
  "${keep_awake[@]}" "$PYTHON" -u -m omega_transform models --out bench/model-check.json \
    --reserve-gb "$RESERVE_GB" --wait-ram-minutes "$wait_ram_minutes" \
    2>&1 | tee -a "$log"
fi

for mode in $MODES; do
  echo "##### reasoning mode: $mode  ($(date))" | tee -a "$log"
  "${keep_awake[@]}" "$PYTHON" -u -m omega_transform bench --"$mode" --minutes "$MINUTES" \
    --feasible bench/model-check.json --reserve-gb "$RESERVE_GB" \
    --wait-ram-minutes "$wait_ram_minutes" "$@" 2>&1 | tee -a "$log"
done

"$PYTHON" -m omega_transform report | tee -a "$log"
"$PYTHON" -m omega_transform docs | tee -a "$log"
echo "done ($(date)); log: $log" | tee -a "$log"
echo "to publish: git add bench/results bench/model-check.json docs && git commit" \
  | tee -a "$log"

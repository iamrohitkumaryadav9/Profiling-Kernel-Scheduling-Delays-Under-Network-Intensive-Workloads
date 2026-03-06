#!/bin/bash
# 24_run_all_experiments.sh — Execute the full 16-experiment matrix
# Usage: sudo ./24_run_all_experiments.sh [--duration 60] [--runs 3] [--phase baselines|placement|advanced|mitigations|all]
#
# Prerequisites:
#   1. sudo ./scripts/24_setup_testbed.sh setup
#   2. Ensure bpftrace, iperf3, stress-ng are installed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_EXP="$SCRIPT_DIR/24_run_experiment.sh"

# Defaults
DURATION=60
RUNS=3
PHASE="all"

# Parse named arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --duration)  DURATION="$2"; shift 2 ;;
        --runs)      RUNS="$2"; shift 2 ;;
        --phase)     PHASE="$2"; shift 2 ;;
        *)           echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "╔═══════════════════════════════════════════════════╗"
echo "║  Experiment Suite — Phase: ${PHASE}                  "
echo "║  Duration: ${DURATION}s per run, ${RUNS} runs each   "
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# ─── Baseline Experiments ────────────────────────────────────────

if [[ "$PHASE" == "all" || "$PHASE" == "baselines" ]]; then
echo "▶ Phase 1: Baselines (E1–E4)"

bash "$RUN_EXP" --exp E1 \
    --cpu-stress none --net-load low --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E2 \
    --cpu-stress none --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E3 \
    --cpu-stress heavy --net-load low --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E4 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"
fi

# ─── Softirq Placement & Pinning Experiments ─────────────────────

if [[ "$PHASE" == "all" || "$PHASE" == "placement" ]]; then
echo ""
echo "▶ Phase 2: Softirq Placement & Pinning (E5–E8)"

bash "$RUN_EXP" --exp E5 \
    --cpu-stress heavy --net-load high --rps-placement rps_pinned \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E6 \
    --cpu-stress heavy --net-load high --rps-placement rps_spread \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E7 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin pinned --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E8 \
    --cpu-stress heavy --net-load high --rps-placement rps_pinned \
    --app-pin pinned --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"
fi

# ─── Advanced Experiments ────────────────────────────────────────

if [[ "$PHASE" == "all" || "$PHASE" == "advanced" ]]; then
echo ""
echo "▶ Phase 3: CFS, Softirq, UDP (E9–E13)"

bash "$RUN_EXP" --exp E9 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin none --cfs lowlatency --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E10 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq forced_ksoftirqd \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E11 \
    --cpu-stress none --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol udp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E12 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol udp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E13 \
    --cpu-stress moderate --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"
fi

# ─── Mitigation Experiments ──────────────────────────────────────

if [[ "$PHASE" == "all" || "$PHASE" == "mitigations" ]]; then
echo ""
echo "▶ Phase 4: Mitigations (E14–E16)"

bash "$RUN_EXP" --exp E14 \
    --cpu-stress heavy --net-load high --rps-placement rps_spread \
    --app-pin pinned --cfs lowlatency --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

# NOTE: E15 requires RPS spread + SO_BUSY_POLL setup. E16 requires SO_BUSY_POLL only.
# See 24_PROJECT_BLUEPRINT.md for configuration commands.

bash "$RUN_EXP" --exp E15 \
    --cpu-stress heavy --net-load high --rps-placement rps_spread \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"

bash "$RUN_EXP" --exp E16 \
    --cpu-stress heavy --net-load high --rps-placement default \
    --app-pin none --cfs default --softirq default \
    --protocol tcp --duration "$DURATION" --runs "$RUNS"
fi

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║  Phase '${PHASE}' complete!                        "
echo "║  Data directory: ./data/                           ║"
echo "╚═══════════════════════════════════════════════════╝"

#!/bin/bash
# 24_run_experiment.sh — Orchestrate a single experiment run
# Usage: sudo ./24_run_experiment.sh --exp E4 --cpu-stress heavy --net-load high \
#        --rps-placement default --app-pin none --cfs default --softirq default \
#        --protocol tcp --duration 60 --runs 3

set -euo pipefail

# ─── Defaults ────────────────────────────────────────────────────
EXP_NAME="E1"
CPU_STRESS="none"           # none | moderate | heavy
NET_LOAD="low"              # low | high
RPS_PLACEMENT="default"     # default | rps_pinned | rps_spread
APP_PIN="none"              # none | pinned
CFS_TUNING="default"        # default | lowlatency
SOFTIRQ_MODE="default"      # default | forced_ksoftirqd
PROTOCOL="tcp"              # tcp | udp
DURATION=60
RUNS=3
DATA_ROOT="./data"
SERVER_IP="10.0.0.1"
WARMUP=10
COOLDOWN=10

# ─── Parse arguments ────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --exp)           EXP_NAME="$2";       shift 2 ;;
        --cpu-stress)    CPU_STRESS="$2";      shift 2 ;;
        --net-load)      NET_LOAD="$2";        shift 2 ;;
        --rps-placement) RPS_PLACEMENT="$2";   shift 2 ;;
        --app-pin)       APP_PIN="$2";         shift 2 ;;
        --cfs)           CFS_TUNING="$2";      shift 2 ;;
        --softirq)       SOFTIRQ_MODE="$2";    shift 2 ;;
        --protocol)      PROTOCOL="$2";        shift 2 ;;
        --duration)      DURATION="$2";        shift 2 ;;
        --runs)          RUNS="$2";            shift 2 ;;
        --data-root)     DATA_ROOT="$2";       shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

NUM_CPUS=$(nproc)
TOOLS_DIR="$(dirname "$0")/../ebpf_tools"
BPFTRACE="${BPFTRACE:-bpftrace}"

echo "============================================="
echo " Experiment: $EXP_NAME"
echo " CPU stress: $CPU_STRESS | Net load: $NET_LOAD"
echo " RPS placement: $RPS_PLACEMENT | App pin: $APP_PIN"
echo " CFS: $CFS_TUNING | Softirq: $SOFTIRQ_MODE"
echo " Protocol: $PROTOCOL | Duration: ${DURATION}s | Runs: $RUNS"
echo "============================================="

# ─── Helper functions ────────────────────────────────────────────

save_sysctl_defaults() {
    local f="$1/sysctl_backup.txt"
    sysctl kernel.sched_min_granularity_ns \
           kernel.sched_wakeup_granularity_ns \
           kernel.sched_latency_ns \
           net.core.netdev_budget \
           net.core.netdev_budget_usecs \
           net.core.busy_poll \
           net.core.busy_read \
           2>/dev/null > "$f" || true
}

restore_sysctl_defaults() {
    local f="$1/sysctl_backup.txt"
    [[ -f "$f" ]] && while read -r line; do
        sysctl -w "$line" 2>/dev/null || true
    done < "$f"
}

apply_rps_placement() {
    # veth-srv lives inside the 'srv' namespace — must access RPS sysfs via nsenter
    systemctl stop irqbalance 2>/dev/null || true

    local rps_path="/sys/class/net/veth-srv/queues/rx-0/rps_cpus"
    local rps_flow_path="/sys/class/net/veth-srv/queues/rx-0/rps_flow_cnt"

    case "$1" in
        default)
            # Clear RPS (softirq runs on whichever CPU receives the packet)
            ip netns exec srv bash -c "echo 0 > $rps_path" 2>/dev/null || true
            echo "  RPS: default (no steering)"
            ;;
        rps_pinned)
            # Pin all softirq to CPU 0
            ip netns exec srv bash -c "echo 1 > $rps_path" 2>/dev/null || true
            ip netns exec srv bash -c "echo 32768 > $rps_flow_path" 2>/dev/null || true
            echo 32768 > /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || true
            echo "  RPS: pinned to CPU 0"
            ;;
        rps_spread)
            # Spread softirq across all CPUs
            local mask=$(printf '%x' $(( (1 << NUM_CPUS) - 1 )))
            ip netns exec srv bash -c "echo $mask > $rps_path" 2>/dev/null || true
            ip netns exec srv bash -c "echo 32768 > $rps_flow_path" 2>/dev/null || true
            echo 32768 > /proc/sys/net/core/rps_sock_flow_entries 2>/dev/null || true
            echo "  RPS: spread across all CPUs (mask=0x$mask)"
            ;;
    esac
}

apply_cfs_tuning() {
    case "$1" in
        default)
            # Typical defaults
            sysctl -w kernel.sched_min_granularity_ns=3000000 2>/dev/null || true
            sysctl -w kernel.sched_wakeup_granularity_ns=4000000 2>/dev/null || true
            sysctl -w kernel.sched_latency_ns=24000000 2>/dev/null || true
            ;;
        lowlatency)
            sysctl -w kernel.sched_min_granularity_ns=1000000 2>/dev/null || true
            sysctl -w kernel.sched_wakeup_granularity_ns=500000 2>/dev/null || true
            sysctl -w kernel.sched_latency_ns=4000000 2>/dev/null || true
            ;;
    esac
}

apply_softirq_mode() {
    case "$1" in
        default)
            sysctl -w net.core.netdev_budget=300 2>/dev/null || true
            sysctl -w net.core.netdev_budget_usecs=8000 2>/dev/null || true
            ;;
        forced_ksoftirqd)
            sysctl -w net.core.netdev_budget=50 2>/dev/null || true
            sysctl -w net.core.netdev_budget_usecs=2000 2>/dev/null || true
            ;;
    esac
}

start_cpu_stress() {
    case "$1" in
        none)
            echo "  No CPU stress"
            ;;
        moderate)
            stress-ng --cpu 2 --cpu-method matrixprod --timeout $((DURATION + WARMUP + 10))s &
            STRESS_PID=$!
            echo "  Moderate CPU stress (2 cores), PID=$STRESS_PID"
            ;;
        heavy)
            stress-ng --cpu "$NUM_CPUS" --cpu-method matrixprod --timeout $((DURATION + WARMUP + 10))s &
            STRESS_PID=$!
            echo "  Heavy CPU stress ($NUM_CPUS cores), PID=$STRESS_PID"
            ;;
    esac
}

stop_cpu_stress() {
    if [[ -n "${STRESS_PID:-}" ]]; then
        kill "$STRESS_PID" 2>/dev/null || true
        wait "$STRESS_PID" 2>/dev/null || true
        unset STRESS_PID
    fi
    pkill -9 stress-ng 2>/dev/null || true
}

start_server() {
    # Start memcached (primary workload) in the srv namespace
    ip netns exec srv memcached -d -m 256 -t 2 -l "$SERVER_IP" -p 11211 -u nobody 2>/dev/null || true
    echo "  memcached started in srv namespace (port 11211)"

    # Start iperf3 server (for bandwidth-saturation experiments)
    ip netns exec srv iperf3 -s -D --logfile /tmp/iperf3_server.log
    echo "  iperf3 server started in srv namespace"

    # For E15/E16 (busy-poll experiments): start custom echo server with SO_BUSY_POLL
    if [[ "$EXP_NAME" == "E15" || "$EXP_NAME" == "E16" ]]; then
        local echo_bin="$TOOLS_DIR/24_busy_poll_echo_server"
        if [[ ! -x "$echo_bin" ]]; then
            echo "  [!] Compiling 24_busy_poll_echo_server..."
            gcc -O2 -o "$echo_bin" "$TOOLS_DIR/24_busy_poll_echo_server.c" -lpthread
        fi
        # Enable kernel-side busy poll support
        sysctl -w net.core.busy_poll=50 2>/dev/null || true
        sysctl -w net.core.busy_read=50 2>/dev/null || true
        ip netns exec srv "$echo_bin" "$SERVER_IP" 9999 50 &
        ECHO_SERVER_PID=$!
        echo "  24_busy_poll_echo_server started on port 9999 (SO_BUSY_POLL=50us), PID=$ECHO_SERVER_PID"
    fi
}

stop_server() {
    if [[ -n "${ECHO_SERVER_PID:-}" ]]; then
        kill "$ECHO_SERVER_PID" 2>/dev/null || true
        wait "$ECHO_SERVER_PID" 2>/dev/null || true
        unset ECHO_SERVER_PID
    fi
    pkill -f 24_busy_poll_echo_server 2>/dev/null || true
    pkill memcached 2>/dev/null || true
    pkill iperf3 2>/dev/null || true
    sleep 1
}

start_ebpf_probes() {
    local outdir="$1"

    # NOTE: bpftrace v0.21+ sends ALL output (histograms + printf) to stdout.
    # We use -q to suppress "Attaching N probes..." and capture everything
    # in a single raw file, then split CSV vs histograms in post-processing.

    "$BPFTRACE" -q "$TOOLS_DIR/24_sched_delay.bt"    > "$outdir/sched_delay_raw.txt" 2>&1 &
    PROBE_PIDS+=($!)

    "$BPFTRACE" -q "$TOOLS_DIR/24_softirq_net.bt"    > "$outdir/softirq_net_raw.txt" 2>&1 &
    PROBE_PIDS+=($!)

    "$BPFTRACE" -q "$TOOLS_DIR/24_net_drops.bt"      > "$outdir/net_drops_raw.txt" 2>&1 &
    PROBE_PIDS+=($!)

    "$BPFTRACE" -q "$TOOLS_DIR/24_cpu_migrations.bt" > "$outdir/cpu_migrations_raw.txt" 2>&1 &
    PROBE_PIDS+=($!)

    bash "$TOOLS_DIR/24_proc_pollers.sh" "$outdir" "$((WARMUP + DURATION + 5))" &
    PROBE_PIDS+=($!)

    echo "  Started ${#PROBE_PIDS[@]} eBPF/proc probes"
}

split_bpftrace_output() {
    # Post-process raw bpftrace output: extract CSV lines vs histogram/summary
    local outdir="$1"

    for tool in sched_delay softirq_net net_drops cpu_migrations; do
        local raw="$outdir/${tool}_raw.txt"
        [[ -f "$raw" ]] || continue

        # CSV lines: start with a digit (timestamp) or contain the header
        grep -E '^(timestamp_ns|interval_s|[0-9])' "$raw" > "$outdir/${tool}.csv" 2>/dev/null || true

        # Everything else is histogram/summary data
        grep -vE '^(timestamp_ns|interval_s|[0-9])' "$raw" > "$outdir/${tool}_summary.txt" 2>/dev/null || true
    done
    echo "  Split raw bpftrace output → .csv + _summary.txt"
}

stop_ebpf_probes() {
    for p in "${PROBE_PIDS[@]:-}"; do
        kill "$p" 2>/dev/null || true
    done
    wait "${PROBE_PIDS[@]}" 2>/dev/null || true
    PROBE_PIDS=()
}

run_load() {
    local outdir="$1"
    local bandwidth=""
    local udp_flag=""

    case "$NET_LOAD" in
        low)  bandwidth="-b 100M" ;;
        high) bandwidth="-b 0"    ;;  # unlimited
    esac

    [[ "$PROTOCOL" == "udp" ]] && udp_flag="-u"

    echo "  Running load for ${DURATION}s (warmup=${WARMUP}s)..."

    # Warmup (no measurement)
    ip netns exec cli iperf3 -c "$SERVER_IP" -t "$WARMUP" $bandwidth $udp_flag \
        --json > /dev/null 2>&1 || true

    # Actual measurement
    local pin_flag=""
    if [[ "$APP_PIN" == "pinned" ]]; then
        pin_flag="taskset -c 2,3"
    fi

    ip netns exec cli $pin_flag iperf3 -c "$SERVER_IP" -t "$DURATION" $bandwidth $udp_flag \
        --json > "$outdir/iperf3_result.json" 2>&1 || true
}

# ─── Main loop ───────────────────────────────────────────────────

declare -a PROBE_PIDS

for run in $(seq 1 "$RUNS"); do
    RUN_DIR="$DATA_ROOT/$EXP_NAME/run_$run"
    mkdir -p "$RUN_DIR"

    echo ""
    echo "--- Run $run/$RUNS ---"

    # Save and apply configuration
    save_sysctl_defaults "$RUN_DIR"
    apply_rps_placement "$RPS_PLACEMENT"
    apply_cfs_tuning "$CFS_TUNING"
    apply_softirq_mode "$SOFTIRQ_MODE"

    # Save experiment metadata
    cat > "$RUN_DIR/metadata.json" <<EOF
{
    "experiment": "$EXP_NAME",
    "run": $run,
    "cpu_stress": "$CPU_STRESS",
    "net_load": "$NET_LOAD",
    "rps_placement": "$RPS_PLACEMENT",
    "app_pin": "$APP_PIN",
    "cfs_tuning": "$CFS_TUNING",
    "softirq_mode": "$SOFTIRQ_MODE",
    "protocol": "$PROTOCOL",
    "duration_s": $DURATION,
    "num_cpus": $NUM_CPUS,
    "kernel": "$(uname -r)",
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

    # Start server
    start_server

    # Start probes
    start_ebpf_probes "$RUN_DIR"

    # Start CPU stressor
    start_cpu_stress "$CPU_STRESS"

    # Stabilize
    sleep 2

    # Run load
    run_load "$RUN_DIR"

    # Stop everything
    echo "  Stopping..."
    stop_cpu_stress
    stop_ebpf_probes
    split_bpftrace_output "$RUN_DIR"
    stop_server

    # Restore defaults
    restore_sysctl_defaults "$RUN_DIR"

    # Cooldown
    echo "  Cooldown ${COOLDOWN}s..."
    sleep "$COOLDOWN"

    echo "  Data saved to: $RUN_DIR"
done

echo ""
echo "============================================="
echo " Experiment $EXP_NAME complete ($RUNS runs)"
echo " Data: $DATA_ROOT/$EXP_NAME/"
echo "============================================="

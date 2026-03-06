#!/bin/bash
# 24_proc_pollers.sh — Collect /proc metrics at 1-second intervals
# Usage: sudo ./24_proc_pollers.sh <output_dir> <duration_secs>

set -euo pipefail

OUTPUT_DIR="${1:-.}"
DURATION="${2:-60}"

mkdir -p "$OUTPUT_DIR"

echo "[proc_pollers] Collecting for ${DURATION}s → $OUTPUT_DIR"

# ─── CPU utilization ────────────────────────────────────────────
collect_cpu() {
    local f="$OUTPUT_DIR/cpu_util.csv"
    echo "timestamp,cpu,user,nice,system,idle,iowait,irq,softirq,steal" > "$f"
    for i in $(seq 1 "$DURATION"); do
        local ts
        ts=$(date +%s.%N)
        while IFS=' ' read -r cpuname user nice system idle iowait irq softirq steal _rest; do
            [[ "$cpuname" =~ ^cpu[0-9]+$ ]] || continue
            echo "$ts,$cpuname,$user,$nice,$system,$idle,$iowait,$irq,$softirq,$steal" >> "$f"
        done < /proc/stat
        sleep 1
    done
}

# ─── Softnet stats ──────────────────────────────────────────────
collect_softnet() {
    local f="$OUTPUT_DIR/softnet_stat.csv"
    echo "timestamp,cpu_idx,processed,dropped,time_squeeze" > "$f"
    for i in $(seq 1 "$DURATION"); do
        local ts
        ts=$(date +%s.%N)
        local cpu_idx=0
        while read -r line; do
            local processed dropped squeeze
            processed=$(echo "$line" | awk '{print $1}')
            dropped=$(echo "$line" | awk '{print $2}')
            squeeze=$(echo "$line" | awk '{print $3}')
            echo "$ts,$cpu_idx,0x$processed,0x$dropped,0x$squeeze" >> "$f"
            cpu_idx=$((cpu_idx + 1))
        done < /proc/net/softnet_stat
        sleep 1
    done
}

# ─── TCP SNMP stats (header-aware parsing) ──────────────────────
# /proc/net/snmp has paired lines: a header line then a values line
# with identical prefix. We match field names from the header to
# extract the correct column indices, making this stable across
# kernel versions.
collect_tcp() {
    local f="$OUTPUT_DIR/tcp_stats.csv"
    echo "timestamp,retrans_segs,in_segs,out_segs" > "$f"
    for i in $(seq 1 "$DURATION"); do
        local ts
        ts=$(date +%s.%N)

        # Read both lines: header then values
        local header values
        header=$(awk '/^Tcp:/ && /RetransSegs/ {print; exit}' /proc/net/snmp)
        values=$(awk '/^Tcp:/ && !/RetransSegs/ {print; exit}' /proc/net/snmp)

        if [[ -n "$header" && -n "$values" ]]; then
            # Convert to arrays
            IFS=' ' read -ra hdr_arr <<< "$header"
            IFS=' ' read -ra val_arr <<< "$values"

            local retrans="" in_segs="" out_segs=""
            for idx in "${!hdr_arr[@]}"; do
                case "${hdr_arr[$idx]}" in
                    RetransSegs) retrans="${val_arr[$idx]}" ;;
                    InSegs)      in_segs="${val_arr[$idx]}" ;;
                    OutSegs)     out_segs="${val_arr[$idx]}" ;;
                esac
            done
            echo "$ts,${retrans:-0},${in_segs:-0},${out_segs:-0}" >> "$f"
        fi
        sleep 1
    done
}

# ─── Socket stats ───────────────────────────────────────────────
collect_sockstat() {
    local f="$OUTPUT_DIR/sockstat.csv"
    echo "timestamp,tcp_inuse,tcp_mem_pages" > "$f"
    for i in $(seq 1 "$DURATION"); do
        local ts
        ts=$(date +%s.%N)
        local inuse mem
        inuse=$(awk '/^TCP:/{print $3}' /proc/net/sockstat)
        mem=$(awk '/^TCP:/{print $NF}' /proc/net/sockstat)
        echo "$ts,$inuse,$mem" >> "$f"
        sleep 1
    done
}

# ─── Interrupts snapshot ────────────────────────────────────────
collect_interrupts() {
    local f="$OUTPUT_DIR/interrupts.csv"
    echo "timestamp,snapshot" > "$f"
    for i in $(seq 1 "$DURATION"); do
        local ts
        ts=$(date +%s.%N)
        # Capture veth-related interrupts only
        local data
        data=$(grep -i 'veth\|virtio\|eth' /proc/interrupts 2>/dev/null | tr '\n' '|' || echo "none")
        echo "$ts,$data" >> "$f"
        sleep 1
    done
}

# Run all pollers in background
collect_cpu &
collect_softnet &
collect_tcp &
collect_sockstat &
collect_interrupts &

echo "[proc_pollers] Started 5 pollers, PID=$$"
wait
echo "[proc_pollers] Done."

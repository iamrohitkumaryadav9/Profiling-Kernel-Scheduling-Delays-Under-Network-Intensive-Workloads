#!/usr/bin/env python3
"""
24_timeseries_plots.py — Generate 3-axis time-series plots
Shows Throughput + Scheduling Delay + Softirq CPU% over time for key experiments.

Usage:
    python3 analysis/24_timeseries_plots.py
"""

import os
import sys
import json
import csv
import re

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy not found. Install with: pip3 install numpy")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
except ImportError:
    print("ERROR: matplotlib not found. Install with: pip3 install matplotlib")
    sys.exit(1)

# ─── Configuration ───────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
PLOT_DIR = os.path.join(os.path.dirname(__file__), '..', 'plots')

# Key experiments to plot time-series for
EXPERIMENTS = {
    'E1': 'No stress, low net (baseline)',
    'E2': 'No stress, high net',
    'E4': 'Heavy stress, high net (worst case)',
    'E9': 'CFS low-latency tuning',
    'E13': 'Moderate stress, high net (threshold)',
    'E16': 'SO_BUSY_POLL only',
}

COLORS = {
    'E1':  '#2ecc71',   # green
    'E2':  '#3498db',   # blue
    'E4':  '#e74c3c',   # red
    'E9':  '#9b59b6',   # purple
    'E13': '#f39c12',   # orange
    'E16': '#1abc9c',   # teal
}


# ─── Data Parsing ────────────────────────────────────────────────

def load_iperf3_throughput(exp, run=1):
    """Load per-second throughput from iperf3 JSON."""
    path = os.path.join(DATA_DIR, exp, f'run_{run}', 'iperf3_result.json')
    if not os.path.exists(path):
        return [], []
    with open(path) as f:
        data = json.load(f)
    times = []
    throughput_gbps = []
    for interval in data.get('intervals', []):
        s = interval.get('sum', {})
        t = (s.get('start', 0) + s.get('end', 0)) / 2
        bps = s.get('bits_per_second', 0)
        times.append(t)
        throughput_gbps.append(bps / 1e9)
    return times, throughput_gbps


def load_cpu_softirq_pct(exp, run=1):
    """Load per-second softirq CPU% from cpu_util.csv (delta-based)."""
    path = os.path.join(DATA_DIR, exp, f'run_{run}', 'cpu_util.csv')
    if not os.path.exists(path):
        return [], []

    # Parse CSV — each row has: timestamp, cpu, user, nice, system, idle, iowait, irq, softirq, steal
    rows_by_ts = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = float(row['timestamp'])
                softirq = int(row['softirq'])
                user = int(row['user'])
                nice = int(row['nice'])
                system = int(row['system'])
                idle = int(row['idle'])
                iowait = int(row['iowait'])
                irq = int(row['irq'])
                steal = int(row['steal'])
                total = user + nice + system + idle + iowait + irq + softirq + steal
            except (ValueError, KeyError):
                continue

            if ts not in rows_by_ts:
                rows_by_ts[ts] = {'softirq': 0, 'total': 0}
            rows_by_ts[ts]['softirq'] += softirq
            rows_by_ts[ts]['total'] += total

    timestamps = sorted(rows_by_ts.keys())
    if len(timestamps) < 2:
        return [], []

    t0 = timestamps[0]
    times = []
    softirq_pct = []
    for i in range(1, len(timestamps)):
        ts_prev = timestamps[i - 1]
        ts_curr = timestamps[i]
        d_softirq = rows_by_ts[ts_curr]['softirq'] - rows_by_ts[ts_prev]['softirq']
        d_total = rows_by_ts[ts_curr]['total'] - rows_by_ts[ts_prev]['total']
        pct = (d_softirq / d_total * 100) if d_total > 0 else 0
        times.append(ts_curr - t0)
        softirq_pct.append(pct)

    return times, softirq_pct


def load_sched_delay_timeseries(exp, run=1, bucket_sec=1.0):
    """Load sampled scheduling delay events and compute per-second p50/p99."""
    path = os.path.join(DATA_DIR, exp, f'run_{run}', 'sched_delay.csv')
    if not os.path.exists(path):
        return [], [], []

    # Parse sampled events
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('timestamp_ns') or not line[0].isdigit():
                continue
            parts = line.split(',')
            if len(parts) < 5:
                continue
            try:
                ts_ns = int(parts[0])
                delay_us = float(parts[4])
                events.append((ts_ns, delay_us))
            except ValueError:
                continue

    if not events:
        return [], [], []

    events.sort(key=lambda x: x[0])
    t0_ns = events[0][0]

    # Bucket events into 1-second intervals
    max_time_s = (events[-1][0] - t0_ns) / 1e9
    n_buckets = int(max_time_s / bucket_sec) + 1

    buckets = [[] for _ in range(n_buckets)]
    for ts_ns, delay_us in events:
        sec = (ts_ns - t0_ns) / 1e9
        bucket_idx = min(int(sec / bucket_sec), n_buckets - 1)
        buckets[bucket_idx].append(delay_us)

    times = []
    p50_vals = []
    p99_vals = []
    for i, bucket in enumerate(buckets):
        if len(bucket) >= 3:  # need min samples
            times.append(i * bucket_sec)
            p50_vals.append(np.percentile(bucket, 50))
            p99_vals.append(np.percentile(bucket, 99))

    return times, p50_vals, p99_vals


# ─── Plotting ────────────────────────────────────────────────────

def plot_3axis_timeseries(exp, label, run=1):
    """Create a 3-axis time-series plot for one experiment."""
    # Load data
    tp_times, tp_gbps = load_iperf3_throughput(exp, run)
    si_times, si_pct = load_cpu_softirq_pct(exp, run)
    sd_times, sd_p50, sd_p99 = load_sched_delay_timeseries(exp, run)

    if not tp_times and not si_times and not sd_times:
        print(f"  ⚠ No data for {exp} run {run}, skipping")
        return None

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    fig.suptitle(f'{exp}: {label}\n(Time-Series, Run {run})', fontsize=14, fontweight='bold')

    color1 = '#2ecc71'
    color2 = '#e74c3c'
    color3 = '#3498db'

    # --- Axis 1: Throughput ---
    if tp_times:
        ax1.plot(tp_times, tp_gbps, color=color1, linewidth=1.5, alpha=0.8)
        ax1.fill_between(tp_times, 0, tp_gbps, alpha=0.15, color=color1)
    ax1.set_ylabel('Throughput (Gbps)', fontsize=11, color=color1)
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax1.set_title('Network Throughput', fontsize=10, loc='left')

    # --- Axis 2: Scheduling Delay ---
    if sd_times:
        ax2.plot(sd_times, sd_p99, color=color2, linewidth=1.5, alpha=0.8, label='p99')
        ax2.plot(sd_times, sd_p50, color='#e67e22', linewidth=1.0, alpha=0.6, label='p50')
        ax2.fill_between(sd_times, sd_p50, sd_p99, alpha=0.1, color=color2)
        ax2.legend(loc='upper right', fontsize=9)
    ax2.set_ylabel('Sched Delay (μs)', fontsize=11, color=color2)
    ax2.tick_params(axis='y', labelcolor=color2)
    ax2.set_yscale('log')
    ax2.set_ylim(bottom=1)
    ax2.grid(True, alpha=0.3, which='both')
    ax2.set_title('Scheduling Delay (sampled events)', fontsize=10, loc='left')

    # --- Axis 3: Softirq CPU% ---
    if si_times:
        ax3.plot(si_times, si_pct, color=color3, linewidth=1.5, alpha=0.8)
        ax3.fill_between(si_times, 0, si_pct, alpha=0.15, color=color3)
    ax3.set_ylabel('Softirq CPU%', fontsize=11, color=color3)
    ax3.tick_params(axis='y', labelcolor=color3)
    ax3.set_ylim(bottom=0)
    ax3.grid(True, alpha=0.3)
    ax3.set_xlabel('Time (seconds)', fontsize=11)
    ax3.set_title('Total Softirq CPU Utilization', fontsize=10, loc='left')

    plt.tight_layout()
    return fig


def plot_overlay_comparison():
    """Create an overlay comparison of key experiments on shared axes."""
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle('Time-Series Comparison: Key Experiments\n'
                 '(Throughput + Scheduling Delay + Softirq CPU%)',
                 fontsize=14, fontweight='bold')

    for exp, label in EXPERIMENTS.items():
        color = COLORS[exp]
        short = f"{exp}"

        # Throughput
        tp_times, tp_gbps = load_iperf3_throughput(exp, run=1)
        if tp_times:
            ax1.plot(tp_times, tp_gbps, color=color, linewidth=1.2, alpha=0.7, label=short)

        # Scheduling delay (p99)
        sd_times, sd_p50, sd_p99 = load_sched_delay_timeseries(exp, run=1)
        if sd_times:
            ax2.plot(sd_times, sd_p99, color=color, linewidth=1.2, alpha=0.7, label=short)

        # Softirq CPU%
        si_times, si_pct = load_cpu_softirq_pct(exp, run=1)
        if si_times:
            ax3.plot(si_times, si_pct, color=color, linewidth=1.2, alpha=0.7, label=short)

    ax1.set_ylabel('Throughput (Gbps)', fontsize=11)
    ax1.set_ylim(bottom=0)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper right', fontsize=8, ncol=3)
    ax1.set_title('Network Throughput', fontsize=10, loc='left')

    ax2.set_ylabel('p99 Sched Delay (μs)', fontsize=11)
    ax2.set_yscale('log')
    ax2.set_ylim(bottom=1)
    ax2.grid(True, alpha=0.3, which='both')
    ax2.legend(loc='upper right', fontsize=8, ncol=3)
    ax2.set_title('Scheduling Delay p99 (from sampled events)', fontsize=10, loc='left')

    ax3.set_ylabel('Softirq CPU%', fontsize=11)
    ax3.set_ylim(bottom=0)
    ax3.grid(True, alpha=0.3)
    ax3.set_xlabel('Time (seconds)', fontsize=11)
    ax3.legend(loc='upper right', fontsize=8, ncol=3)
    ax3.set_title('Total Softirq CPU Utilization (%)', fontsize=10, loc='left')

    plt.tight_layout()
    return fig


# ─── Main ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Time-Series 3-Axis Plot Generator")
    print("=" * 60)

    os.makedirs(PLOT_DIR, exist_ok=True)

    # 1. Individual experiment time-series
    print("\n─── Individual Experiment Time-Series ───")
    for exp, label in EXPERIMENTS.items():
        print(f"\n  Processing {exp}: {label}...")
        fig = plot_3axis_timeseries(exp, label, run=1)
        if fig:
            fname = f'24_timeseries_{exp.lower()}.png'
            path = os.path.join(PLOT_DIR, fname)
            fig.savefig(path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            print(f"  ✓ Saved: {path}")

    # 2. Overlay comparison
    print("\n─── Overlay Comparison ───")
    fig = plot_overlay_comparison()
    fname = '24_timeseries_comparison.png'
    path = os.path.join(PLOT_DIR, fname)
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✓ Saved: {path}")

    # 3. Stress progression (E1 vs E13 vs E4) - detailed comparison
    print("\n─── Stress Progression (E1 → E13 → E4) ───")
    fig, axes = plt.subplots(3, 3, figsize=(16, 10), sharex=True)
    fig.suptitle('Stress Progression: No Stress → Moderate → Heavy\n'
                 '(Throughput + Scheduling Delay + Softirq CPU%)',
                 fontsize=14, fontweight='bold')

    progression = [('E1', 'No stress'), ('E13', 'Moderate stress'), ('E4', 'Heavy stress')]
    colors_prog = ['#2ecc71', '#f39c12', '#e74c3c']

    for col, (exp, title) in enumerate(progression):
        tp_t, tp_g = load_iperf3_throughput(exp, 1)
        sd_t, sd_p50, sd_p99 = load_sched_delay_timeseries(exp, 1)
        si_t, si_p = load_cpu_softirq_pct(exp, 1)
        c = colors_prog[col]

        # Throughput
        if tp_t:
            axes[0][col].plot(tp_t, tp_g, color=c, linewidth=1.2)
            axes[0][col].fill_between(tp_t, 0, tp_g, alpha=0.15, color=c)
        axes[0][col].set_title(f'{exp}: {title}', fontsize=10, fontweight='bold')
        axes[0][col].set_ylim(bottom=0)
        axes[0][col].grid(True, alpha=0.3)
        if col == 0:
            axes[0][col].set_ylabel('Throughput\n(Gbps)', fontsize=9)

        # Scheduling delay
        if sd_t:
            axes[1][col].plot(sd_t, sd_p99, color=c, linewidth=1.2, label='p99')
            axes[1][col].plot(sd_t, sd_p50, color=c, linewidth=0.8, alpha=0.5, linestyle='--', label='p50')
            axes[1][col].legend(fontsize=7, loc='upper right')
        axes[1][col].set_yscale('log')
        axes[1][col].set_ylim(1, 100000)
        axes[1][col].grid(True, alpha=0.3, which='both')
        if col == 0:
            axes[1][col].set_ylabel('Sched Delay\n(μs, log)', fontsize=9)

        # Softirq
        if si_t:
            axes[2][col].plot(si_t, si_p, color=c, linewidth=1.2)
            axes[2][col].fill_between(si_t, 0, si_p, alpha=0.15, color=c)
        axes[2][col].set_ylim(bottom=0)
        axes[2][col].grid(True, alpha=0.3)
        axes[2][col].set_xlabel('Time (s)', fontsize=9)
        if col == 0:
            axes[2][col].set_ylabel('Softirq\nCPU%', fontsize=9)

    plt.tight_layout()
    path = os.path.join(PLOT_DIR, '24_timeseries_stress_progression.png')
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  ✓ Saved: {path}")

    print("\n" + "=" * 60)
    print("  Time-Series Plots Complete")
    print("=" * 60)


if __name__ == '__main__':
    main()

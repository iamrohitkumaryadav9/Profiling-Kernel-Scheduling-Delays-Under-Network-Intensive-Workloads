#!/usr/bin/env python3
"""
Phase 7: Derived Metrics & Final Visualization
Generates all 15+ plots and a summary metrics CSV.
Designed to be fast on slow filesystems (uses subprocess for large file reads).
"""

import os
import sys
import re
import json
import subprocess
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

DATA_DIR = os.path.expanduser("~/Desktop/GRS Project/MT25037/data")
PLOT_DIR = os.path.expanduser("~/Desktop/GRS Project/MT25037/plots")
os.makedirs(PLOT_DIR, exist_ok=True)

# Experiment order and labels
ALL_EXPS = [
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8",
    "E9", "E10", "E11", "E12", "E13", "E14", "E15", "E16"
]
EXP_LABELS = {
    "E1": "No stress\nLow net",
    "E2": "No stress\nHigh net",
    "E3": "Heavy CPU\nLow net",
    "E4": "Heavy CPU\nHigh net",
    "E5": "E4+RPS→CPU0",
    "E6": "E4+RPS→all",
    "E7": "E4+App pin",
    "E8": "E4+RPS+pin",
    "E9": "E4+CFS lowlat",
    "E10": "E4+ksoftirqd",
    "E11": "UDP no stress",
    "E12": "UDP+heavy",
    "E13": "Moderate CPU",
    "E14": "Combined mit",
    "E15": "RPS+busy_poll",
    "E16": "busy_poll only",
}

# Color scheme
COLORS = {
    "baselines": ["#2ecc71", "#27ae60", "#e74c3c", "#c0392b"],  # E1-E4
    "placement": ["#3498db", "#2980b9", "#9b59b6", "#8e44ad"],  # E5-E8
    "advanced": ["#f39c12", "#e67e22", "#1abc9c", "#16a085"],    # E9-E12
    "mitigations": ["#e91e63", "#9c27b0", "#673ab7", "#3f51b5"], # E13-E16
}

def get_color(exp):
    idx = ALL_EXPS.index(exp)
    phase = idx // 4
    keys = list(COLORS.keys())
    return COLORS[keys[phase]][idx % 4]


# ─── Parsing Functions (fast, subprocess-based) ────────────────────────

def parse_suffix(s):
    s = s.strip()
    if s.endswith('K'): return int(s[:-1]) * 1024
    if s.endswith('M'): return int(s[:-1]) * 1024 * 1024
    if s.endswith('G'): return int(s[:-1]) * 1024 * 1024 * 1024
    return int(s)

def get_cumulative_histogram(exp, run="run_1"):
    """Extract the LAST @runq_delay_us histogram from sched_delay_summary.txt"""
    fpath = os.path.join(DATA_DIR, exp, run, "sched_delay_summary.txt")
    if not os.path.exists(fpath):
        return []
    try:
        result = subprocess.run(
            ["cat", fpath], capture_output=True, text=True, timeout=30
        )
        content = result.stdout
    except:
        return []

    # Find all @runq_delay_us blocks
    blocks = []
    current = []
    in_block = False
    for line in content.split('\n'):
        if '@runq_delay_us:' in line:
            if current:
                blocks.append(current)
            current = []
            in_block = True
            continue
        if in_block:
            line = line.strip()
            if line.startswith('['):
                # Parse: [low, high)  count |bars|
                m = re.match(r'\[([0-9KMG]+)(?:,\s*([0-9KMG]+))?\)?\s+(\d+)\s+\|', line)
                if m:
                    low = parse_suffix(m.group(1))
                    high = parse_suffix(m.group(2)) if m.group(2) else low + 1
                    count = int(m.group(3))
                    current.append((low, high, count))
            elif line == '' or line.startswith('@'):
                if current:
                    blocks.append(current)
                    current = []
                in_block = '@runq_delay_us:' in line
                if in_block:
                    current = []

    if current:
        blocks.append(current)

    return blocks[-1] if blocks else []

def histogram_to_cdf(buckets):
    """Convert histogram buckets to CDF arrays (x_us, cdf)"""
    if not buckets:
        return np.array([]), np.array([])
    total = sum(c for _, _, c in buckets)
    if total == 0:
        return np.array([]), np.array([])
    cumsum = 0
    xs, ys = [0], [0]
    for low, high, count in buckets:
        cumsum += count
        xs.append(high)
        ys.append(cumsum / total)
    return np.array(xs, dtype=float), np.array(ys, dtype=float)

def get_percentiles(buckets, percentiles=[50, 90, 95, 99, 99.9]):
    """Get percentile values from histogram buckets"""
    if not buckets:
        return {p: None for p in percentiles}
    total = sum(c for _, _, c in buckets)
    if total == 0:
        return {p: None for p in percentiles}
    result = {}
    for pct in percentiles:
        target = total * pct / 100.0
        cumsum = 0
        for low, high, count in buckets:
            cumsum += count
            if cumsum >= target:
                result[pct] = high
                break
        else:
            result[pct] = buckets[-1][1] if buckets else None
    return result

def get_voluntary_switches(exp, run="run_1"):
    """Extract @voluntary count from sched_delay_summary.txt"""
    fpath = os.path.join(DATA_DIR, exp, run, "sched_delay_summary.txt")
    if not os.path.exists(fpath):
        return None
    try:
        result = subprocess.run(
            ["grep", "@voluntary", fpath], capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split('\n')
        if lines and lines[-1]:
            return int(lines[-1].split()[-1])
    except:
        pass
    return None

def get_softirq_per_cpu(exp, run="run_1"):
    """Extract last @net_rx_count per-CPU map from softirq_net_summary.txt"""
    fpath = os.path.join(DATA_DIR, exp, run, "softirq_net_summary.txt")
    if not os.path.exists(fpath):
        return {}
    try:
        result = subprocess.run(
            ["cat", fpath], capture_output=True, text=True, timeout=30
        )
        content = result.stdout
    except:
        return {}

    pattern = re.compile(r'^@net_rx_count\[(\d+)\]:\s+(\d+)')
    blocks = []
    current = {}
    for line in content.split('\n'):
        m = pattern.match(line.strip())
        if m:
            current[int(m.group(1))] = int(m.group(2))
        else:
            if current:
                blocks.append(current)
                current = {}
    if current:
        blocks.append(current)
    return blocks[-1] if blocks else {}

def get_softnet_deltas(exp, run="run_1"):
    """Get time_squeeze and dropped from softnet_stat.csv (first/last lines)"""
    fpath = os.path.join(DATA_DIR, exp, run, "softnet_stat.csv")
    if not os.path.exists(fpath):
        return {"time_squeeze": 0, "dropped": 0}
    try:
        head = subprocess.run(
            ["head", "-2", fpath], capture_output=True, text=True, timeout=5
        ).stdout.strip().split('\n')
        tail = subprocess.run(
            ["tail", "-1", fpath], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except:
        return {"time_squeeze": 0, "dropped": 0}

    if len(head) < 2:
        return {"time_squeeze": 0, "dropped": 0}

    header = head[0].split(',')
    first = head[1].split(',')
    last = tail.split(',')

    result = {}
    for col in ["time_squeeze", "dropped"]:
        if col in header:
            idx = header.index(col)
            try:
                result[col] = int(last[idx]) - int(first[idx])
            except:
                result[col] = 0
        else:
            result[col] = 0
    return result

def get_tcp_retransmits(exp, run="run_1"):
    """Get TCP retransmit delta from tcp_stats.csv"""
    fpath = os.path.join(DATA_DIR, exp, run, "tcp_stats.csv")
    if not os.path.exists(fpath):
        return 0
    try:
        head = subprocess.run(
            ["head", "-2", fpath], capture_output=True, text=True, timeout=5
        ).stdout.strip().split('\n')
        tail = subprocess.run(
            ["tail", "-1", fpath], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except:
        return 0

    if len(head) < 2:
        return 0
    header = head[0].split(',')
    first = head[1].split(',')
    last = tail.split(',')

    for col in ["RetransSegs", "retransmits"]:
        if col in header:
            idx = header.index(col)
            try:
                return int(last[idx]) - int(first[idx])
            except:
                pass
    return 0

def get_cpu_migrations(exp, run="run_1"):
    """Get total CPU migrations from cpu_migrations_summary.txt"""
    fpath = os.path.join(DATA_DIR, exp, run, "cpu_migrations_summary.txt")
    if not os.path.exists(fpath):
        return 0
    try:
        result = subprocess.run(
            ["grep", "@migrations", fpath], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split('\n')
        if lines and lines[-1]:
            return int(lines[-1].split()[-1])
    except:
        pass
    return 0


# ─── Data Collection ───────────────────────────────────────────────────

def collect_all_metrics():
    """Collect all metrics for all experiments"""
    print("Collecting metrics for all experiments...")
    metrics = {}
    for exp in ALL_EXPS:
        exp_dir = os.path.join(DATA_DIR, exp)
        if not os.path.exists(exp_dir):
            print(f"  SKIP {exp} (no data)")
            continue

        print(f"  Processing {exp}...")
        # Average across runs
        runs = [d for d in os.listdir(exp_dir) if d.startswith("run_")]
        if not runs:
            continue

        all_pcts = []
        all_vol = []
        all_softnet = []
        all_retransmits = []
        all_migrations = []

        for run in sorted(runs):
            buckets = get_cumulative_histogram(exp, run)
            if buckets:
                pcts = get_percentiles(buckets)
                all_pcts.append(pcts)

            vol = get_voluntary_switches(exp, run)
            if vol is not None:
                all_vol.append(vol)

            sn = get_softnet_deltas(exp, run)
            all_softnet.append(sn)

            rt = get_tcp_retransmits(exp, run)
            all_retransmits.append(rt)

            mig = get_cpu_migrations(exp, run)
            all_migrations.append(mig)

        # Average percentiles
        avg_pcts = {}
        if all_pcts:
            for p in [50, 90, 95, 99, 99.9]:
                vals = [d[p] for d in all_pcts if d.get(p) is not None]
                avg_pcts[p] = np.mean(vals) if vals else None

        # Get first run's softirq distribution for heatmap
        softirq_cpu = get_softirq_per_cpu(exp, runs[0])

        metrics[exp] = {
            "percentiles": avg_pcts,
            "vol_switches": int(np.mean(all_vol)) if all_vol else 0,
            "time_squeeze": int(np.mean([s["time_squeeze"] for s in all_softnet])),
            "dropped": int(np.mean([s["dropped"] for s in all_softnet])),
            "retransmits": int(np.mean(all_retransmits)),
            "migrations": int(np.mean(all_migrations)),
            "softirq_per_cpu": softirq_cpu,
            "num_runs": len(runs),
        }

    return metrics


# ─── Plot Functions ────────────────────────────────────────────────────

def plot_1_all_cdf(metrics):
    """Plot 1: CDF overlay of all experiments"""
    print("  Plot 1: All experiments CDF...")
    fig, ax = plt.subplots(figsize=(14, 8))
    for exp in ALL_EXPS:
        if exp not in metrics:
            continue
        buckets = get_cumulative_histogram(exp, "run_1")
        x, y = histogram_to_cdf(buckets)
        if len(x) == 0:
            continue
        ax.plot(x, y, label=exp, color=get_color(exp), linewidth=1.5)

    ax.set_xscale('log')
    ax.set_xlim(1, 1e6)
    ax.set_ylim(0.9, 1.001)
    ax.set_xlabel('Run-queue Delay (μs)', fontsize=12)
    ax.set_ylabel('CDF', fontsize=12)
    ax.set_title('Run-queue Delay CDF — All 16 Experiments (p90+ region)', fontsize=14)
    ax.axhline(y=0.99, color='red', linestyle='--', alpha=0.5, label='p99')
    ax.axhline(y=0.999, color='darkred', linestyle=':', alpha=0.5, label='p99.9')
    ax.legend(fontsize=8, ncol=4, loc='lower right')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_runqueue_delay_cdf_all.png"), dpi=150)
    plt.close()

def plot_2_percentile_bars(metrics):
    """Plot 2: Bar chart of p50, p99, p99.9 across experiments"""
    print("  Plot 2: Percentile bar chart...")
    exps = [e for e in ALL_EXPS if e in metrics and metrics[e]["percentiles"].get(99)]
    if not exps:
        return

    p50 = [metrics[e]["percentiles"].get(50, 0) or 0 for e in exps]
    p99 = [metrics[e]["percentiles"].get(99, 0) or 0 for e in exps]
    p999 = [metrics[e]["percentiles"].get(99.9, 0) or 0 for e in exps]

    x = np.arange(len(exps))
    width = 0.25

    fig, ax = plt.subplots(figsize=(16, 7))
    ax.bar(x - width, p50, width, label='p50', color='#2ecc71', alpha=0.8)
    ax.bar(x, p99, width, label='p99', color='#e74c3c', alpha=0.8)
    ax.bar(x + width, p999, width, label='p99.9', color='#8e44ad', alpha=0.8)

    ax.set_yscale('log')
    ax.set_ylabel('Delay (μs)', fontsize=12)
    ax.set_xlabel('Experiment', fontsize=12)
    ax.set_title('Scheduling Delay Percentiles Across All Experiments', fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(exps, rotation=45, ha='right')
    ax.legend(fontsize=11)

    # Add 1ms threshold line
    ax.axhline(y=1000, color='orange', linestyle='--', alpha=0.7, label='1ms threshold')
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_percentile_comparison.png"), dpi=150)
    plt.close()

def plot_3_softirq_heatmap(metrics):
    """Plot 3: Heatmap of per-CPU softirq distribution"""
    print("  Plot 3: Softirq CPU heatmap...")
    exps_with_data = [e for e in ALL_EXPS if e in metrics and metrics[e]["softirq_per_cpu"]]
    if not exps_with_data:
        print("    No softirq data found, skipping")
        return

    max_cpu = max(max(metrics[e]["softirq_per_cpu"].keys()) for e in exps_with_data) + 1
    max_cpu = min(max_cpu, 20)  # Cap at 20 CPUs

    data = np.zeros((len(exps_with_data), max_cpu))
    for i, exp in enumerate(exps_with_data):
        cpu_data = metrics[exp]["softirq_per_cpu"]
        total = sum(cpu_data.values()) or 1
        for cpu, count in cpu_data.items():
            if cpu < max_cpu:
                data[i, cpu] = count / total * 100

    fig, ax = plt.subplots(figsize=(14, 8))
    cmap = LinearSegmentedColormap.from_list('custom', ['#f0f0f0', '#3498db', '#e74c3c', '#2c3e50'])
    im = ax.imshow(data, aspect='auto', cmap=cmap, vmin=0, vmax=30)
    ax.set_xticks(range(max_cpu))
    ax.set_xticklabels([f'CPU{i}' for i in range(max_cpu)], rotation=45, fontsize=8)
    ax.set_yticks(range(len(exps_with_data)))
    ax.set_yticklabels(exps_with_data)
    ax.set_xlabel('CPU', fontsize=12)
    ax.set_ylabel('Experiment', fontsize=12)
    ax.set_title('Softirq NET_RX Distribution by CPU (% of total)', fontsize=14)
    plt.colorbar(im, ax=ax, label='% of total softirq')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_softirq_cpu_heatmap.png"), dpi=150)
    plt.close()

def plot_4_mitigation_comparison(metrics):
    """Plot 4: Before/after mitigation comparison"""
    print("  Plot 4: Mitigation comparison...")
    groups = {
        "Baseline\n(E4)": "E4",
        "RPS spread\n(E6)": "E6",
        "App pin\n(E7)": "E7",
        "RPS+pin\n(E8)": "E8",
        "CFS lowlat\n(E9)": "E9",
        "ksoftirqd\n(E10)": "E10",
        "Combined\n(E14)": "E14",
        "RPS+bpoll\n(E15)": "E15",
        "bpoll only\n(E16)": "E16",
    }

    labels = []
    p99_vals = []
    colors = []
    for label, exp in groups.items():
        if exp in metrics and metrics[exp]["percentiles"].get(99):
            labels.append(label)
            p99_vals.append(metrics[exp]["percentiles"][99])
            colors.append('#c0392b' if exp == "E4" else '#3498db')

    if not labels:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(range(len(labels)), p99_vals, color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel('p99 Delay (μs)', fontsize=12)
    ax.set_title('Mitigation Effectiveness: p99 Scheduling Delay vs Baseline (E4)', fontsize=14)
    ax.axhline(y=p99_vals[0], color='red', linestyle='--', alpha=0.5, linewidth=2)

    # Add value labels
    for bar, val in zip(bars, p99_vals):
        if val >= 1000:
            label = f'{val/1000:.1f}ms'
        else:
            label = f'{val:.0f}μs'
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 100, label,
                ha='center', va='bottom', fontsize=9, fontweight='bold')

    ax.set_yscale('log')
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_mitigation_p99_comparison.png"), dpi=150)
    plt.close()

def plot_5_boxplot(metrics):
    """Plot 5: Box plot of delay distributions"""
    print("  Plot 5: Box plot distributions...")
    exps = [e for e in ALL_EXPS if e in metrics]
    if not exps:
        return

    # Generate synthetic samples from histogram for box plots
    box_data = []
    for exp in exps:
        buckets = get_cumulative_histogram(exp, "run_1")
        if not buckets:
            box_data.append([0])
            continue
        samples = []
        for low, high, count in buckets:
            if count > 0:
                mid = (low + high) / 2
                # Subsample to keep manageable
                n = min(count, 100)
                samples.extend([mid] * n)
        box_data.append(samples if samples else [0])

    fig, ax = plt.subplots(figsize=(16, 7))
    bp = ax.boxplot(box_data, labels=exps, patch_artist=True, showfliers=False,
                    whis=[5, 95])

    for i, patch in enumerate(bp['boxes']):
        patch.set_facecolor(get_color(exps[i]))
        patch.set_alpha(0.7)

    ax.set_yscale('log')
    ax.set_ylabel('Run-queue Delay (μs)', fontsize=12)
    ax.set_xlabel('Experiment', fontsize=12)
    ax.set_title('Scheduling Delay Distribution (5th-95th percentile, no outliers)', fontsize=14)
    ax.grid(True, alpha=0.3, axis='y')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_delay_distribution_boxplot.png"), dpi=150)
    plt.close()

def plot_6_context_switches(metrics):
    """Plot 6: Voluntary context switch rate comparison"""
    print("  Plot 6: Context switches...")
    exps = [e for e in ALL_EXPS if e in metrics and metrics[e]["vol_switches"] > 0]
    if not exps:
        return

    vals = [metrics[e]["vol_switches"] / 1e6 for e in exps]  # In millions
    colors = [get_color(e) for e in exps]

    fig, ax = plt.subplots(figsize=(14, 6))
    bars = ax.bar(range(len(exps)), vals, color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(exps)))
    ax.set_xticklabels(exps, rotation=45, ha='right')
    ax.set_ylabel('Voluntary Context Switches (millions)', fontsize=12)
    ax.set_title('Voluntary Context Switch Count per Experiment', fontsize=14)
    ax.grid(True, alpha=0.3, axis='y')

    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f'{val:.1f}M', ha='center', va='bottom', fontsize=8)

    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_voluntary_context_switches.png"), dpi=150)
    plt.close()

def plot_7_retransmits(metrics):
    """Plot 7: TCP retransmit comparison"""
    print("  Plot 7: TCP retransmits...")
    tcp_exps = [e for e in ALL_EXPS if e in metrics and e not in ["E11", "E12"]]
    if not tcp_exps:
        return

    vals = [metrics[e]["retransmits"] for e in tcp_exps]
    colors = [get_color(e) for e in tcp_exps]

    fig, ax = plt.subplots(figsize=(14, 6))
    bars = ax.bar(range(len(tcp_exps)), vals, color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(tcp_exps)))
    ax.set_xticklabels(tcp_exps, rotation=45, ha='right')
    ax.set_ylabel('TCP Retransmit Segments', fontsize=12)
    ax.set_title('TCP Retransmits per Experiment (60s average)', fontsize=14)
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_tcp_retransmit_comparison.png"), dpi=150)
    plt.close()

def plot_8_packet_drops(metrics):
    """Plot 8: Packet drop rate comparison"""
    print("  Plot 8: Packet drops...")
    exps = [e for e in ALL_EXPS if e in metrics]
    if not exps:
        return

    drops = [metrics[e]["dropped"] for e in exps]
    squeezed = [metrics[e]["time_squeeze"] for e in exps]

    x = np.arange(len(exps))
    width = 0.35

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(x - width/2, drops, width, label='Packet Drops', color='#e74c3c', alpha=0.8)
    ax.bar(x + width/2, squeezed, width, label='Time Squeeze', color='#f39c12', alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(exps, rotation=45, ha='right')
    ax.set_ylabel('Count', fontsize=12)
    ax.set_title('Softnet Statistics: Drops & Time Squeeze', fontsize=14)
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_softnet_drops_squeeze.png"), dpi=150)
    plt.close()

def plot_9_cpu_migrations(metrics):
    """Plot 9: CPU migration comparison"""
    print("  Plot 9: CPU migrations...")
    exps = [e for e in ALL_EXPS if e in metrics and metrics[e]["migrations"] > 0]
    if not exps:
        print("    No migration data, skipping")
        return

    vals = [metrics[e]["migrations"] for e in exps]
    colors = [get_color(e) for e in exps]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(range(len(exps)), vals, color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(exps)))
    ax.set_xticklabels(exps, rotation=45, ha='right')
    ax.set_ylabel('CPU Migrations', fontsize=12)
    ax.set_title('Task CPU Migrations per Experiment', fontsize=14)
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_task_cpu_migrations.png"), dpi=150)
    plt.close()

def plot_10_scatter_softirq_vs_delay(metrics):
    """Plot 10: Scatter — softirq concentration vs p99 delay"""
    print("  Plot 10: Softirq vs delay scatter...")
    exps = [e for e in ALL_EXPS if e in metrics and metrics[e]["softirq_per_cpu"]
            and metrics[e]["percentiles"].get(99)]

    if not exps:
        print("    No data, skipping")
        return

    ginis = []
    p99s = []
    for exp in exps:
        cpu_data = metrics[exp]["softirq_per_cpu"]
        vals = list(cpu_data.values())
        total = sum(vals) or 1
        fracs = sorted([v/total for v in vals])
        n = len(fracs)
        if n == 0:
            ginis.append(0)
        else:
            gini = sum((2*i - n - 1) * fracs[i] for i in range(n)) / (n * sum(fracs)) if sum(fracs) > 0 else 0
            ginis.append(abs(gini))
        p99s.append(metrics[exp]["percentiles"][99])

    fig, ax = plt.subplots(figsize=(10, 7))
    colors = [get_color(e) for e in exps]
    ax.scatter(ginis, p99s, c=colors, s=120, edgecolors='white', linewidth=1.5, zorder=5)

    for i, exp in enumerate(exps):
        ax.annotate(exp, (ginis[i], p99s[i]), fontsize=9, ha='left',
                    xytext=(5, 5), textcoords='offset points')

    ax.set_xlabel('Softirq Gini Coefficient (concentration)', fontsize=12)
    ax.set_ylabel('p99 Run-queue Delay (μs)', fontsize=12)
    ax.set_title('Softirq Concentration vs Scheduling Delay', fontsize=14)
    ax.set_yscale('log')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_softirq_gini_vs_p99.png"), dpi=150)
    plt.close()

def plot_11_baseline_cdf(metrics):
    """Plot 11: CDF of baselines E1-E4 (stress progression)"""
    print("  Plot 11: Baseline CDF...")
    fig, ax = plt.subplots(figsize=(10, 7))
    for exp in ["E1", "E2", "E3", "E4"]:
        if exp not in metrics:
            continue
        buckets = get_cumulative_histogram(exp, "run_1")
        x, y = histogram_to_cdf(buckets)
        if len(x) == 0:
            continue
        ax.plot(x, y, label=f'{exp} ({EXP_LABELS[exp].replace(chr(10), ", ")})',
                color=get_color(exp), linewidth=2.5)

    ax.set_xscale('log')
    ax.set_xlim(1, 1e6)
    ax.set_ylim(0.8, 1.001)
    ax.set_xlabel('Run-queue Delay (μs)', fontsize=12)
    ax.set_ylabel('CDF', fontsize=12)
    ax.set_title('Baseline Experiments: Effect of Stress on Scheduling Delay', fontsize=14)
    ax.axhline(y=0.99, color='red', linestyle='--', alpha=0.5, label='p99')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_baseline_stress_progression.png"), dpi=150)
    plt.close()

def plot_12_tcp_vs_udp(metrics):
    """Plot 12: TCP vs UDP CDF comparison"""
    print("  Plot 12: TCP vs UDP...")
    fig, ax = plt.subplots(figsize=(10, 7))
    pairs = [("E1", "TCP no stress"), ("E11", "UDP no stress"),
             ("E4", "TCP heavy+high"), ("E12", "UDP heavy+high")]

    colors_tcp_udp = ['#2ecc71', '#e74c3c', '#27ae60', '#c0392b']
    for (exp, label), color in zip(pairs, colors_tcp_udp):
        if exp not in metrics:
            continue
        buckets = get_cumulative_histogram(exp, "run_1")
        x, y = histogram_to_cdf(buckets)
        if len(x) == 0:
            continue
        ls = '--' if 'UDP' in label else '-'
        ax.plot(x, y, label=f'{exp}: {label}', color=color, linewidth=2, linestyle=ls)

    ax.set_xscale('log')
    ax.set_xlim(1, 1e6)
    ax.set_ylim(0.8, 1.001)
    ax.set_xlabel('Run-queue Delay (μs)', fontsize=12)
    ax.set_ylabel('CDF', fontsize=12)
    ax.set_title('H3: TCP vs UDP Scheduling Delay Under Stress', fontsize=14)
    ax.axhline(y=0.99, color='gray', linestyle=':', alpha=0.5)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_tcp_udp_delay_comparison.png"), dpi=150)
    plt.close()

def plot_13_degradation_factors(metrics):
    """Plot 13: Degradation factor (normalized to E1 baseline)"""
    print("  Plot 13: Degradation factors...")
    if "E1" not in metrics or not metrics["E1"]["percentiles"].get(99):
        print("    No E1 data, skipping")
        return

    e1_p99 = metrics["E1"]["percentiles"][99]
    exps = [e for e in ALL_EXPS if e in metrics and metrics[e]["percentiles"].get(99)]
    if not exps:
        return

    factors = [metrics[e]["percentiles"][99] / e1_p99 for e in exps]

    fig, ax = plt.subplots(figsize=(14, 6))
    colors = [get_color(e) for e in exps]
    bars = ax.bar(range(len(exps)), factors, color=colors, alpha=0.85, edgecolor='white')
    ax.set_xticks(range(len(exps)))
    ax.set_xticklabels(exps, rotation=45, ha='right')
    ax.set_ylabel(f'p99 Degradation Factor (vs E1 = {e1_p99}μs)', fontsize=12)
    ax.set_title('Scheduling Delay Degradation Normalized to E1 Baseline', fontsize=14)
    ax.axhline(y=1, color='green', linestyle='--', alpha=0.5, linewidth=2)
    ax.set_yscale('log')
    ax.grid(True, alpha=0.3, axis='y')

    for bar, val in zip(bars, factors):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() * 1.1,
                f'{val:.0f}x', ha='center', va='bottom', fontsize=8, fontweight='bold')

    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_normalized_degradation_factors.png"), dpi=150)
    plt.close()

def plot_14_per_cpu_rps(metrics):
    """Plot 14: Per-CPU softirq with/without RPS"""
    print("  Plot 14: Per-CPU softirq RPS comparison...")
    compare = ["E4", "E5", "E6", "E7"]
    avail = [e for e in compare if e in metrics and metrics[e]["softirq_per_cpu"]]
    if len(avail) < 2:
        print("    Not enough data, skipping")
        return

    fig, axes = plt.subplots(1, len(avail), figsize=(5*len(avail), 5), sharey=True)
    if len(avail) == 1:
        axes = [axes]

    for ax, exp in zip(axes, avail):
        cpu_data = metrics[exp]["softirq_per_cpu"]
        total = sum(cpu_data.values()) or 1
        cpus = sorted(cpu_data.keys())[:20]
        fracs = [cpu_data.get(c, 0) / total * 100 for c in cpus]

        ax.bar(cpus, fracs, color=get_color(exp), alpha=0.8)
        ax.set_title(f'{exp}\n{EXP_LABELS[exp].replace(chr(10), ", ")}', fontsize=10)
        ax.set_xlabel('CPU')
        if ax == axes[0]:
            ax.set_ylabel('% of NET_RX softirq')
        ax.set_ylim(0, max(max(fracs) * 1.2, 5))
        ax.grid(True, alpha=0.3, axis='y')

    plt.suptitle('Per-CPU Softirq Distribution: RPS Placement Effects', fontsize=14, y=1.02)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_per_cpu_softirq_rps.png"), dpi=150, bbox_inches='tight')
    plt.close()

def plot_15_summary_table(metrics):
    """Plot 15: Summary table as image"""
    print("  Plot 15: Summary table...")
    exps = [e for e in ALL_EXPS if e in metrics]
    if not exps:
        return

    cols = ['Exp', 'p50 (μs)', 'p99 (μs)', 'p99.9 (μs)', 'VolSw (M)', 'Retrans', 'Drops']
    rows = []
    for exp in exps:
        m = metrics[exp]
        p = m["percentiles"]
        rows.append([
            exp,
            f'{p.get(50, 0) or 0:.0f}',
            f'{p.get(99, 0) or 0:.0f}',
            f'{p.get(99.9, 0) or 0:.0f}',
            f'{m["vol_switches"]/1e6:.1f}' if m["vol_switches"] else '—',
            str(m["retransmits"]),
            str(m["dropped"]),
        ])

    fig, ax = plt.subplots(figsize=(14, max(6, len(rows) * 0.4 + 2)))
    ax.axis('off')
    table = ax.table(cellText=rows, colLabels=cols, loc='center',
                     cellLoc='center', colColours=['#3498db']*len(cols))
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.5)

    # Color header
    for j in range(len(cols)):
        table[0, j].set_text_props(color='white', fontweight='bold')

    ax.set_title('Phase 7: Complete Experiment Metrics Summary', fontsize=14, pad=20)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "24_experiment_summary_table.png"), dpi=150, bbox_inches='tight')
    plt.close()

def write_metrics_csv(metrics):
    """Write metrics to CSV file"""
    print("  Writing metrics CSV...")
    csv_path = os.path.join(PLOT_DIR, "24_experiment_metrics.csv")
    with open(csv_path, 'w') as f:
        f.write("experiment,p50_us,p90_us,p95_us,p99_us,p999_us,vol_switches,time_squeeze,dropped,retransmits,migrations\n")
        for exp in ALL_EXPS:
            if exp not in metrics:
                continue
            m = metrics[exp]
            p = m["percentiles"]
            f.write(f'{exp},{p.get(50,"")},{p.get(90,"")},{p.get(95,"")},{p.get(99,"")},{p.get(99.9,"")},'
                    f'{m["vol_switches"]},{m["time_squeeze"]},{m["dropped"]},{m["retransmits"]},{m["migrations"]}\n')
    print(f"    → {csv_path}")


# ─── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Phase 7: Derived Metrics & Visualization")
    print("=" * 60)

    metrics = collect_all_metrics()
    print(f"\nCollected data for {len(metrics)} experiments")
    print()

    print("Generating 15 plots...")
    plot_1_all_cdf(metrics)
    plot_2_percentile_bars(metrics)
    plot_3_softirq_heatmap(metrics)
    plot_4_mitigation_comparison(metrics)
    plot_5_boxplot(metrics)
    plot_6_context_switches(metrics)
    plot_7_retransmits(metrics)
    plot_8_packet_drops(metrics)
    plot_9_cpu_migrations(metrics)
    plot_10_scatter_softirq_vs_delay(metrics)
    plot_11_baseline_cdf(metrics)
    plot_12_tcp_vs_udp(metrics)
    plot_13_degradation_factors(metrics)
    plot_14_per_cpu_rps(metrics)
    plot_15_summary_table(metrics)
    write_metrics_csv(metrics)

    print()
    print("=" * 60)
    print(f"Done! {len([f for f in os.listdir(PLOT_DIR) if f.endswith('.png')])} plots in {PLOT_DIR}")
    print("=" * 60)

    # Print quick summary
    print("\n--- Quick Percentile Summary ---")
    print(f"{'Exp':<5} {'p50':>8} {'p99':>8} {'p99.9':>10} {'VolSw':>10}")
    for exp in ALL_EXPS:
        if exp not in metrics:
            continue
        p = metrics[exp]["percentiles"]
        vs = metrics[exp]["vol_switches"]
        p50 = f'{p.get(50,0) or 0:.0f}μs'
        p99 = f'{p.get(99,0) or 0:.0f}μs'
        p999 = f'{p.get(99.9,0) or 0:.0f}μs'
        print(f'{exp:<5} {p50:>8} {p99:>8} {p999:>10} {vs:>10}')

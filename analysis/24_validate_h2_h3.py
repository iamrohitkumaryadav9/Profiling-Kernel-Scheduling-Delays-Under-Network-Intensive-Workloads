#!/usr/bin/env python3
"""
24_validate_h2_h3.py — Validate H2 (ksoftirqd) and H3 (TCP vs UDP) hypotheses.

H2: E10 (forced ksoftirqd via NAPI budget) should show different latency vs E4
H3: E12 (UDP+stress) vs E4 (TCP+stress) should show different per-packet delay
E13: Moderate stress identifies the CPU contention inflection point

Usage:
    python3 analysis/24_validate_h2_h3.py --data-dir ./data --output-dir ./plots
"""

import argparse
import re
import os
import sys
import csv
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
except ImportError:
    print("ERROR: matplotlib not found. Install with: pip3 install matplotlib")
    sys.exit(1)

# Import histogram parser from our existing module (name starts with digit, need importlib)
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import importlib
_ph = importlib.import_module("24_parse_histograms")
parse_last_histogram = _ph.parse_last_histogram
buckets_to_cdf = _ph.buckets_to_cdf
load_experiment_cdf = _ph.load_experiment_cdf
_fmt_us = _ph._fmt_us


# ─── Softnet Stat Parsing ────────────────────────────────────────

def parse_softnet_stat(filepath):
    """
    Parse softnet_stat.csv with hex values.
    Returns {cpu: {'processed': int, 'dropped': int, 'time_squeeze': int}} for LAST timestamp.
    """
    if not os.path.exists(filepath):
        return {}

    result = {}
    with open(filepath, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)  # timestamp,cpu_idx,processed,dropped,time_squeeze

        for row in reader:
            if len(row) < 5:
                continue
            try:
                cpu = int(row[1])
                processed = int(row[2], 16)
                dropped = int(row[3], 16)
                time_squeeze = int(row[4], 16)
                result[cpu] = {
                    'processed': processed,
                    'dropped': dropped,
                    'time_squeeze': time_squeeze,
                }
            except (ValueError, IndexError):
                continue

    return result


def get_softnet_deltas(filepath):
    """
    Get the CHANGE in softnet stats between first and last sample.
    Uses subprocess head/tail to avoid reading entire large file.
    Returns total time_squeeze delta and per-cpu deltas.
    """
    import subprocess
    if not os.path.exists(filepath):
        return {}, 0

    try:
        # Read first 21 lines (header + 20 CPUs) and last 20 lines
        head_result = subprocess.run(
            ['head', '-21', filepath], capture_output=True, text=True, timeout=5)
        tail_result = subprocess.run(
            ['tail', '-20', filepath], capture_output=True, text=True, timeout=5)

        first_vals = {}
        for line in head_result.stdout.strip().split('\n')[1:]:  # skip header
            parts = line.split(',')
            if len(parts) >= 5:
                cpu = int(parts[1])
                first_vals[cpu] = {
                    'processed': int(parts[2], 16),
                    'time_squeeze': int(parts[4], 16)
                }

        last_vals = {}
        for line in tail_result.stdout.strip().split('\n'):
            parts = line.split(',')
            if len(parts) >= 5:
                cpu = int(parts[1])
                last_vals[cpu] = {
                    'processed': int(parts[2], 16),
                    'time_squeeze': int(parts[4], 16)
                }

        deltas = {}
        total_squeeze = 0
        for cpu in last_vals:
            if cpu in first_vals:
                sq_delta = last_vals[cpu]['time_squeeze'] - first_vals[cpu]['time_squeeze']
                proc_delta = last_vals[cpu]['processed'] - first_vals[cpu]['processed']
                deltas[cpu] = {'time_squeeze': sq_delta, 'processed': proc_delta}
                total_squeeze += sq_delta

        return deltas, total_squeeze
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"    Warning: Could not parse {filepath}: {e}")
        return {}, 0


# ─── Context Switch Parsing ──────────────────────────────────────

def get_voluntary_switches(filepath):
    """Extract @voluntary count from sched_delay_summary.txt."""
    if not os.path.exists(filepath):
        return 0
    with open(filepath, 'r') as f:
        for line in f:
            m = re.match(r'@voluntary:\s+(\d+)', line.strip())
            if m:
                return int(m.group(1))
    return 0


# ─── TCP Stats Parsing ───────────────────────────────────────────

def get_tcp_retransmits(filepath):
    """Get total retransmit delta from tcp_stats.csv using head/tail for speed."""
    import subprocess
    if not os.path.exists(filepath):
        return 0

    try:
        # First data line (line 2)
        head_result = subprocess.run(
            ['sed', '-n', '2p', filepath], capture_output=True, text=True, timeout=5)
        tail_result = subprocess.run(
            ['tail', '-1', filepath], capture_output=True, text=True, timeout=5)

        first_line = head_result.stdout.strip()
        last_line = tail_result.stdout.strip()

        if first_line and last_line:
            first_retrans = int(first_line.split(',')[1])
            last_retrans = int(last_line.split(',')[1])
            return last_retrans - first_retrans
    except (subprocess.TimeoutExpired, Exception):
        pass
    return 0


# ─── Plotting ────────────────────────────────────────────────────

def plot_h2_comparison(data_dir, output_dir, num_cpus=20):
    """
    Generate H2 plots: E4 vs E10 CDF overlay + softnet time_squeeze comparison.
    """
    plt.style.use('dark_background')
    fig = plt.figure(figsize=(16, 6))
    fig.patch.set_facecolor('#1a1a2e')
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1.3, 1])

    # ─── CDF Overlay ─────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor('#16213e')

    configs = {
        'E1': {'label': 'E1: baseline', 'color': '#2ecc71', 'ls': '-'},
        'E4': {'label': 'E4: heavy+high (default)', 'color': '#e74c3c', 'ls': '-'},
        'E10': {'label': 'E10: forced ksoftirqd', 'color': '#3498db', 'ls': '--'},
    }

    for exp, meta in configs.items():
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) > 0:
            ax1.plot(x, cdf, label=meta['label'], color=meta['color'],
                     linestyle=meta['ls'], linewidth=2.5, alpha=0.9)

    ax1.set_xscale('log')
    ax1.set_xlabel('Runqueue Delay (μs)', fontsize=12, color='#e0e0e0')
    ax1.set_ylabel('CDF', fontsize=12, color='#e0e0e0')
    ax1.set_title('H2: ksoftirqd Effect on Scheduling Delay', fontsize=13,
                   color='#ffffff', fontweight='bold')
    ax1.set_ylim(0, 1.02)
    ax1.set_xlim(left=0.8)
    ax1.grid(True, alpha=0.15)
    ax1.tick_params(colors='#b0b0b0')

    for p, label in [(0.95, 'p95'), (0.99, 'p99')]:
        ax1.axhline(y=p, color='#ffffff', alpha=0.15, linestyle='--', linewidth=0.8)
        ax1.text(1, p + 0.01, label, color='#ffffff', alpha=0.4, fontsize=9)

    legend = ax1.legend(fontsize=10, framealpha=0.8, facecolor='#1a1a2e', edgecolor='#444444')
    for t in legend.get_texts():
        t.set_color('#e0e0e0')

    # ─── Time Squeeze + Voluntary Switches Bar Chart ─────────
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor('#16213e')

    exps_to_compare = ['E4', 'E10']
    colors = ['#e74c3c', '#3498db']
    squeeze_totals = []
    vol_switches = []

    for exp in exps_to_compare:
        # Average across runs
        runs_dir = os.path.join(data_dir, exp)
        runs = sorted([d for d in os.listdir(runs_dir) if d.startswith('run_')])
        sq_total = 0
        vs_total = 0
        for run in runs:
            _, sq = get_softnet_deltas(os.path.join(runs_dir, run, 'softnet_stat.csv'))
            vs = get_voluntary_switches(os.path.join(runs_dir, run, 'sched_delay_summary.txt'))
            sq_total += sq
            vs_total += vs
        squeeze_totals.append(sq_total / len(runs) if runs else 0)
        vol_switches.append(vs_total / len(runs) if runs else 0)

    x_pos = np.arange(len(exps_to_compare))
    width = 0.35

    sq_vals = [s / 1000 for s in squeeze_totals]
    vs_vals = [v / 1000 for v in vol_switches]

    bars1 = ax2.bar(x_pos - width / 2, sq_vals, width,
                     color=colors, alpha=0.85, label='Time Squeeze (K)')
    ax2_twin = ax2.twinx()
    bars2 = ax2_twin.bar(x_pos + width / 2, vs_vals, width,
                          color=colors, alpha=0.4, hatch='///', label='Vol. Switches (K)')

    ax2.set_ylim(0, max(sq_vals) * 1.2 if sq_vals and max(sq_vals) > 0 else 5)
    ax2_twin.set_ylim(0, max(vs_vals) * 1.2 if vs_vals and max(vs_vals) > 0 else 5)

    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(exps_to_compare)
    ax2.set_ylabel('Time Squeeze Events (K)', fontsize=11, color='#e0e0e0')
    ax2_twin.set_ylabel('Voluntary Switches (K)', fontsize=11, color='#e0e0e0')
    ax2.set_title('Softnet Backpressure Metrics', fontsize=13, color='#ffffff', fontweight='bold')
    ax2.tick_params(colors='#b0b0b0')
    ax2_twin.tick_params(colors='#b0b0b0')
    ax2.grid(axis='y', alpha=0.15)

    # Add value labels
    for bar, val in zip(bars1, squeeze_totals):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (ax2.get_ylim()[1]*0.02),
                 f'{val:.0f}', ha='center', va='bottom', color='#e0e0e0', fontsize=9)
    for bar, val in zip(bars2, vol_switches):
        ax2_twin.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (ax2_twin.get_ylim()[1]*0.02),
                       f'{val/1000:.0f}K', ha='center', va='bottom', color='#e0e0e0', fontsize=9)

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h2_ksoftirqd_comparison.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


def plot_h3_comparison(data_dir, output_dir):
    """
    Generate H3 plots: TCP vs UDP CDF overlay + retransmit comparison.
    """
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.patch.set_facecolor('#1a1a2e')
    for ax in (ax1, ax2):
        ax.set_facecolor('#16213e')

    # ─── CDF Overlay ─────────────────────────────────
    configs = {
        'E1': {'label': 'E1: TCP, no stress', 'color': '#2ecc71', 'ls': '-'},
        'E4': {'label': 'E4: TCP, heavy+high', 'color': '#e74c3c', 'ls': '-'},
        'E11': {'label': 'E11: UDP, no stress', 'color': '#3498db', 'ls': '--'},
        'E12': {'label': 'E12: UDP, heavy+high', 'color': '#f39c12', 'ls': '--'},
    }

    for exp, meta in configs.items():
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) > 0:
            ax1.plot(x, cdf, label=meta['label'], color=meta['color'],
                     linestyle=meta['ls'], linewidth=2.5, alpha=0.9)

    ax1.set_xscale('log')
    ax1.set_xlabel('Runqueue Delay (μs)', fontsize=12, color='#e0e0e0')
    ax1.set_ylabel('CDF', fontsize=12, color='#e0e0e0')
    ax1.set_title('H3: TCP vs UDP Scheduling Delay', fontsize=13,
                   color='#ffffff', fontweight='bold')
    ax1.set_ylim(0, 1.02)
    ax1.set_xlim(left=0.8)
    ax1.grid(True, alpha=0.15)
    ax1.tick_params(colors='#b0b0b0')

    for p, label in [(0.95, 'p95'), (0.99, 'p99')]:
        ax1.axhline(y=p, color='#ffffff', alpha=0.15, linestyle='--', linewidth=0.8)
        ax1.text(1, p + 0.01, label, color='#ffffff', alpha=0.4, fontsize=9)

    legend = ax1.legend(fontsize=10, framealpha=0.8, facecolor='#1a1a2e', edgecolor='#444444')
    for t in legend.get_texts():
        t.set_color('#e0e0e0')

    # ─── Percentile Bar Comparison ───────────────────
    exps = ['E1', 'E4', 'E11', 'E12']
    exp_labels = ['E1\nTCP\nNo stress', 'E4\nTCP\nHeavy+High', 'E11\nUDP\nNo stress', 'E12\nUDP\nHeavy+High']
    colors = ['#2ecc71', '#e74c3c', '#3498db', '#f39c12']

    percentiles_data = {'p50': [], 'p95': [], 'p99': []}
    for exp in exps:
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) > 0:
            for pname, pval in [('p50', 0.50), ('p95', 0.95), ('p99', 0.99)]:
                idx = np.searchsorted(cdf, pval)
                val = x[idx] if idx < len(x) else float('nan')
                percentiles_data[pname].append(val)
        else:
            for pname in percentiles_data:
                percentiles_data[pname].append(0)

    x_pos = np.arange(len(exps))
    width = 0.25

    for i, (pname, vals) in enumerate(percentiles_data.items()):
        bars = ax2.bar(x_pos + (i - 1) * width, vals, width,
                       label=pname, alpha=0.85 - i * 0.15)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                         _fmt_us(val), ha='center', va='bottom', color='#e0e0e0',
                         fontsize=7, rotation=45)

    ax2.set_yscale('log')
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(exp_labels, fontsize=9)
    ax2.set_ylabel('Delay (μs, log scale)', fontsize=12, color='#e0e0e0')
    ax2.set_title('Percentile Comparison: TCP vs UDP', fontsize=13,
                   color='#ffffff', fontweight='bold')
    ax2.tick_params(colors='#b0b0b0')
    ax2.grid(axis='y', alpha=0.15)
    legend2 = ax2.legend(fontsize=10, framealpha=0.8, facecolor='#1a1a2e', edgecolor='#444444')
    for t in legend2.get_texts():
        t.set_color('#e0e0e0')

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h3_tcp_vs_udp.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


def plot_contention_threshold(data_dir, output_dir):
    """
    Generate E13 threshold plot: E1 (none) → E13 (moderate) → E3/E4 (heavy)
    to identify where p99 crosses 1ms.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    configs = {
        'E1': {'label': 'E1: No stress', 'color': '#2ecc71', 'ls': '-'},
        'E13': {'label': 'E13: Moderate stress', 'color': '#f39c12', 'ls': '-'},
        'E3': {'label': 'E3: Heavy stress, Low load', 'color': '#e67e22', 'ls': '--'},
        'E4': {'label': 'E4: Heavy stress, High load', 'color': '#e74c3c', 'ls': '-'},
    }

    for exp, meta in configs.items():
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) > 0:
            ax.plot(x, cdf, label=meta['label'], color=meta['color'],
                    linestyle=meta['ls'], linewidth=2.5, alpha=0.9)

    # Mark 1ms threshold
    ax.axvline(x=1000, color='#ff6b6b', alpha=0.5, linestyle=':', linewidth=2)
    ax.text(1100, 0.5, '1ms threshold', color='#ff6b6b', alpha=0.7,
            fontsize=11, rotation=90, va='center')

    ax.set_xscale('log')
    ax.set_xlabel('Runqueue Delay (μs)', fontsize=12, color='#e0e0e0')
    ax.set_ylabel('CDF', fontsize=12, color='#e0e0e0')
    ax.set_title('CPU Contention Threshold: When Does p99 Cross 1ms?',
                  fontsize=14, color='#ffffff', fontweight='bold')
    ax.set_ylim(0, 1.02)
    ax.set_xlim(left=0.8)
    ax.grid(True, alpha=0.15)
    ax.tick_params(colors='#b0b0b0')

    for p, label in [(0.95, 'p95'), (0.99, 'p99')]:
        ax.axhline(y=p, color='#ffffff', alpha=0.15, linestyle='--', linewidth=0.8)
        ax.text(1, p + 0.01, label, color='#ffffff', alpha=0.4, fontsize=9)

    # Add percentile text box
    lines = ["  Exp   p50    p95      p99     p99.9"]
    lines.append("  " + "─" * 38)
    for exp in ['E1', 'E13', 'E3', 'E4']:
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) == 0:
            continue
        p50 = x[np.searchsorted(cdf, 0.50)] if np.any(cdf >= 0.50) else float('nan')
        p95 = x[np.searchsorted(cdf, 0.95)] if np.any(cdf >= 0.95) else float('nan')
        p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')
        p999 = x[np.searchsorted(cdf, 0.999)] if np.any(cdf >= 0.999) else float('nan')
        lines.append(f"  {exp:4s} {_fmt_us(p50):>6s} {_fmt_us(p95):>7s}  {_fmt_us(p99):>7s}  {_fmt_us(p999):>7s}")

    text = '\n'.join(lines)
    ax.text(0.02, 0.55, text, transform=ax.transAxes,
            fontsize=9, fontfamily='monospace',
            color='#c0c0c0', alpha=0.9,
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#1a1a2e',
                      edgecolor='#444444', alpha=0.85),
            verticalalignment='top')

    legend = ax.legend(loc='lower right', fontsize=10, framealpha=0.8,
                       facecolor='#1a1a2e', edgecolor='#444444')
    for t in legend.get_texts():
        t.set_color('#e0e0e0')

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h2h3_contention_threshold.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Validate H2 and H3 hypotheses')
    parser.add_argument('--data-dir', '-d', default='./data')
    parser.add_argument('--output-dir', '-o', default='./plots')
    parser.add_argument('--num-cpus', type=int, default=20)
    args = parser.parse_args()

    print("=" * 60)
    print("  H2 & H3 Validation")
    print("=" * 60)
    print()

    # ─── H2: ksoftirqd Effect ────────────────────────────────

    print("─── H2: ksoftirqd (E4 vs E10) ───")
    print()

    # Percentile comparison
    print(f"{'Experiment':>12s}  {'p50':>8s}  {'p95':>8s}  {'p99':>8s}  {'p99.9':>8s}")
    print("  " + "─" * 44)
    for exp in ['E1', 'E4', 'E10']:
        x, cdf = load_experiment_cdf(args.data_dir, exp)
        if len(x) == 0:
            continue
        p50 = x[np.searchsorted(cdf, 0.50)] if np.any(cdf >= 0.50) else float('nan')
        p95 = x[np.searchsorted(cdf, 0.95)] if np.any(cdf >= 0.95) else float('nan')
        p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')
        p999 = x[np.searchsorted(cdf, 0.999)] if np.any(cdf >= 0.999) else float('nan')
        print(f"  {exp:>8s}  {_fmt_us(p50):>8s}  {_fmt_us(p95):>8s}  {_fmt_us(p99):>8s}  {_fmt_us(p999):>8s}")

    # Time squeeze comparison
    print()
    print("  Softnet time_squeeze (total across all CPUs):")
    for exp in ['E4', 'E10']:
        runs_dir = os.path.join(args.data_dir, exp)
        runs = sorted([d for d in os.listdir(runs_dir) if d.startswith('run_')])
        squeeze_total = 0
        for run in runs:
            _, sq = get_softnet_deltas(os.path.join(runs_dir, run, 'softnet_stat.csv'))
            squeeze_total += sq
        avg_squeeze = squeeze_total / len(runs) if runs else 0
        print(f"    {exp}: {avg_squeeze:.0f} time_squeeze events (avg across {len(runs)} runs)")

    # Voluntary switches
    print()
    print("  Voluntary context switches:")
    for exp in ['E4', 'E10']:
        runs_dir = os.path.join(args.data_dir, exp)
        runs = sorted([d for d in os.listdir(runs_dir) if d.startswith('run_')])
        vs_total = 0
        for run in runs:
            vs = get_voluntary_switches(os.path.join(runs_dir, run, 'sched_delay_summary.txt'))
            vs_total += vs
        avg_vs = vs_total / len(runs) if runs else 0
        print(f"    {exp}: {avg_vs:.0f} voluntary switches (avg)")

    # H2 interpretation
    print()
    print("  H2 Interpretation:")
    x_e4, cdf_e4 = load_experiment_cdf(args.data_dir, 'E4')
    x_e10, cdf_e10 = load_experiment_cdf(args.data_dir, 'E10')
    if len(x_e4) > 0 and len(x_e10) > 0:
        p99_e4 = x_e4[np.searchsorted(cdf_e4, 0.99)]
        p99_e10 = x_e10[np.searchsorted(cdf_e10, 0.99)]
        if p99_e10 < p99_e4:
            print(f"    → E10 p99 ({_fmt_us(p99_e10)}) < E4 p99 ({_fmt_us(p99_e4)})")
            print(f"    → ksoftirqd REDUCES tail latency ✓ (H2 supported)")
        elif p99_e10 == p99_e4:
            print(f"    → E10 p99 ({_fmt_us(p99_e10)}) ≈ E4 p99 ({_fmt_us(p99_e4)})")
            print(f"    → ksoftirqd shows SIMILAR latency (bucket-level tie)")
            print(f"      Check p99.9 for finer differentiation")
        else:
            print(f"    → E10 p99 ({_fmt_us(p99_e10)}) > E4 p99 ({_fmt_us(p99_e4)})")
            print(f"    → ksoftirqd did NOT reduce tail latency (H2 not supported)")

    # ─── H3: TCP vs UDP ──────────────────────────────────────

    print()
    print("─── H3: TCP vs UDP (E4 vs E12, E1 vs E11) ───")
    print()

    print(f"{'Experiment':>12s}  {'Protocol':>8s}  {'p50':>8s}  {'p95':>8s}  {'p99':>8s}  {'p99.9':>8s}")
    print("  " + "─" * 56)
    for exp, proto in [('E1', 'TCP'), ('E4', 'TCP'), ('E11', 'UDP'), ('E12', 'UDP')]:
        x, cdf = load_experiment_cdf(args.data_dir, exp)
        if len(x) == 0:
            continue
        p50 = x[np.searchsorted(cdf, 0.50)] if np.any(cdf >= 0.50) else float('nan')
        p95 = x[np.searchsorted(cdf, 0.95)] if np.any(cdf >= 0.95) else float('nan')
        p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')
        p999 = x[np.searchsorted(cdf, 0.999)] if np.any(cdf >= 0.999) else float('nan')
        print(f"  {exp:>8s}  {proto:>8s}  {_fmt_us(p50):>8s}  {_fmt_us(p95):>8s}  {_fmt_us(p99):>8s}  {_fmt_us(p999):>8s}")

    # Retransmit comparison
    print()
    print("  TCP Retransmits (delta over experiment):")
    for exp in ['E1', 'E4']:
        runs_dir = os.path.join(args.data_dir, exp)
        runs = sorted([d for d in os.listdir(runs_dir) if d.startswith('run_')])
        rt_total = 0
        for run in runs:
            rt = get_tcp_retransmits(os.path.join(runs_dir, run, 'tcp_stats.csv'))
            rt_total += rt
        avg_rt = rt_total / len(runs) if runs else 0
        print(f"    {exp}: {avg_rt:.0f} retransmits (avg)")

    # H3 interpretation
    print()
    print("  H3 Interpretation:")
    x_e4, cdf_e4 = load_experiment_cdf(args.data_dir, 'E4')
    x_e12, cdf_e12 = load_experiment_cdf(args.data_dir, 'E12')
    if len(x_e4) > 0 and len(x_e12) > 0:
        p99_e4 = x_e4[np.searchsorted(cdf_e4, 0.99)]
        p99_e12 = x_e12[np.searchsorted(cdf_e12, 0.99)]
        if p99_e12 < p99_e4:
            print(f"    → E12 UDP p99 ({_fmt_us(p99_e12)}) < E4 TCP p99 ({_fmt_us(p99_e4)})")
            print(f"    → UDP has LOWER per-packet scheduling delay under stress ✓")
        else:
            print(f"    → E12 UDP p99 ({_fmt_us(p99_e12)}) ≥ E4 TCP p99 ({_fmt_us(p99_e4)})")
            print(f"    → UDP does NOT show lower per-packet delay")
            print(f"      This may indicate CPU contention dominates over protocol overhead")

    # ─── E13: Contention Threshold ───────────────────────────

    print()
    print("─── E13: CPU Contention Threshold ───")
    print()

    exps_threshold = ['E1', 'E13', 'E3', 'E4']
    labels_map = {
        'E1': 'No stress', 'E13': 'Moderate stress',
        'E3': 'Heavy + Low', 'E4': 'Heavy + High'
    }

    print(f"{'Experiment':>12s}  {'Config':>16s}  {'p99':>8s}  {'> 1ms?':>8s}")
    print("  " + "─" * 50)
    for exp in exps_threshold:
        x, cdf = load_experiment_cdf(args.data_dir, exp)
        if len(x) == 0:
            continue
        p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')
        over_1ms = "YES ⚠" if p99 >= 1000 else "no"
        print(f"  {exp:>8s}  {labels_map[exp]:>16s}  {_fmt_us(p99):>8s}  {over_1ms:>8s}")

    print()
    print("  Threshold Analysis:")
    x_e13, cdf_e13 = load_experiment_cdf(args.data_dir, 'E13')
    if len(x_e13) > 0:
        p99_e13 = x_e13[np.searchsorted(cdf_e13, 0.99)]
        p99_e1 = x_e4[np.searchsorted(cdf_e4, 0.99)] if len(x_e4) > 0 else float('nan')
        if p99_e13 < 1000:
            print(f"    → E13 p99 = {_fmt_us(p99_e13)} (below 1ms): moderate stress is still manageable")
            print(f"    → The 1ms threshold is crossed between E13 (moderate) and E3/E4 (heavy)")
        else:
            print(f"    → E13 p99 = {_fmt_us(p99_e13)} (above 1ms): even moderate stress crosses 1ms")

    # ─── Generate plots ──────────────────────────────────────

    print()
    print("─── Generating Plots ───")

    plot_h2_comparison(args.data_dir, args.output_dir)
    plot_h3_comparison(args.data_dir, args.output_dir)
    plot_contention_threshold(args.data_dir, args.output_dir)

    print()
    print("=" * 60)
    print("  H2 & H3 Validation Complete")
    print("=" * 60)


if __name__ == '__main__':
    main()

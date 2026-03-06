#!/usr/bin/env python3
"""
24_validate_h4.py — Validate H4: Combined Mitigations Hypothesis

Tests whether combining mitigations (RPS spread, app pinning, CFS tuning,
SO_BUSY_POLL) reduces tail scheduling delay vs the E4 baseline.

Experiments:
  - E4:  Heavy CPU + high net (baseline)
  - E14: RPS spread + app pinning + CFS lowlatency (combined, no busy_poll)
  - E15: RPS spread + SO_BUSY_POLL
  - E16: SO_BUSY_POLL only

Outputs:
  1. CDF overlay of E4 vs mitigations
  2. Voluntary context switch comparison bar chart
  3. Console summary with p99/vol_switches analysis

Usage:
    python3 analysis/24_validate_h4.py --data-dir ./data --output-dir ./plots
"""

import argparse
import os
import sys
import re
import subprocess
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:
    print("ERROR: matplotlib not found. Install with: pip3 install matplotlib")
    sys.exit(1)

# Import shared histogram parsing from 24_parse_histograms.py (name starts with digit, need importlib)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib
_ph = importlib.import_module("24_parse_histograms")
parse_last_histogram = _ph.parse_last_histogram
buckets_to_cdf = _ph.buckets_to_cdf
load_experiment_cdf = _ph.load_experiment_cdf
_fmt_us = _ph._fmt_us


# ─── Context Switch Parsing ─────────────────────────────────────

def get_voluntary_switches(filepath):
    """Extract @voluntary count from sched_delay_summary.txt."""
    if not os.path.exists(filepath):
        return None
    try:
        result = subprocess.run(
            ["grep", "@voluntary", filepath],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split('\n')
        if lines and lines[-1]:
            return int(lines[-1].split()[-1])
    except Exception:
        pass
    return None


def get_voluntary_switches_all_runs(data_dir, experiment):
    """Get voluntary switches for each run of an experiment."""
    exp_dir = os.path.join(data_dir, experiment)
    if not os.path.isdir(exp_dir):
        return []
    runs = sorted([d for d in os.listdir(exp_dir) if d.startswith('run_')])
    values = []
    for run in runs:
        fpath = os.path.join(exp_dir, run, "sched_delay_summary.txt")
        v = get_voluntary_switches(fpath)
        if v is not None:
            values.append(v)
    return values


# ─── Percentile Extraction ───────────────────────────────────────

def parse_suffix(s):
    s = s.strip()
    if s.endswith('K'): return int(s[:-1]) * 1024
    if s.endswith('M'): return int(s[:-1]) * 1024 * 1024
    return int(s)


def get_percentiles_from_file(filepath, percentiles=[50, 90, 95, 99, 99.9]):
    """Parse histogram and compute percentile values."""
    if not os.path.exists(filepath):
        return {p: None for p in percentiles}

    try:
        with open(filepath, 'r') as f:
            content = f.read()
    except Exception:
        return {p: None for p in percentiles}

    # Find last @runq_delay_us block
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

    if not blocks:
        return {p: None for p in percentiles}

    buckets = blocks[-1]
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


# ─── Plotting ────────────────────────────────────────────────────

def plot_mitigation_cdf(data_dir, output_dir):
    """
    Plot CDF overlay: E4 (baseline) vs E14, E15, E16.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    experiments = {
        'E4 (baseline)':              ('E4',  '#e74c3c', '-',  2.5),
        'E14 (RPS+pin+CFS)':         ('E14', '#3498db', '--', 2.0),
        'E15 (RPS+busy_poll)':        ('E15', '#2ecc71', '--', 2.0),
        'E16 (busy_poll only)':       ('E16', '#f39c12', '--', 2.0),
    }

    for label, (exp, color, ls, lw) in experiments.items():
        try:
            x, y = load_experiment_cdf(data_dir, exp)
            ax.plot(x, y, label=label, color=color,
                    linestyle=ls, linewidth=lw)
        except Exception as e:
            print(f"  Warning: Could not load {exp}: {e}")

    ax.set_xscale('log')
    ax.set_xlim(1, 2e5)
    ax.set_ylim(0.85, 1.001)
    ax.set_xlabel('Run-queue Delay (μs)', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_ylabel('CDF', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_title('H4: Combined Mitigations vs Baseline (E4)',
                 fontsize=14, color='#ffffff', fontweight='bold', pad=15)

    ax.axhline(y=0.99, color='#ff6b6b', linestyle=':', alpha=0.6, linewidth=1)
    ax.text(2, 0.9905, 'p99', color='#ff6b6b', fontsize=9, alpha=0.7)
    ax.axhline(y=0.999, color='#ff3333', linestyle=':', alpha=0.4, linewidth=1)
    ax.text(2, 0.9993, 'p99.9', color='#ff3333', fontsize=9, alpha=0.5)

    ax.tick_params(colors='#b0b0b0')
    ax.grid(True, alpha=0.15, color='#ffffff')

    legend = ax.legend(loc='lower right', fontsize=11, framealpha=0.8,
                       facecolor='#1a1a2e', edgecolor='#444444')
    for text in legend.get_texts():
        text.set_color('#e0e0e0')

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h4_mitigations_cdf.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


def plot_context_switches(data_dir, output_dir):
    """
    Bar chart comparing voluntary context switches across E4, E14, E15, E16.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    experiments = ['E4', 'E14', 'E15', 'E16']
    labels = [
        'E4\n(baseline)',
        'E14\n(RPS+pin+CFS)',
        'E15\n(RPS+busy_poll)',
        'E16\n(busy_poll only)',
    ]
    colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12']

    means = []
    stds = []
    per_run = []
    for exp in experiments:
        vals = get_voluntary_switches_all_runs(data_dir, exp)
        per_run.append(vals)
        if vals:
            means.append(np.mean(vals))
            stds.append(np.std(vals))
        else:
            means.append(0)
            stds.append(0)

    x = np.arange(len(experiments))
    bars = ax.bar(x, [m / 1e6 for m in means], color=colors, alpha=0.85,
                  edgecolor='none', yerr=[s / 1e6 for s in stds],
                  capsize=8, error_kw={'color': '#bbbbbb', 'linewidth': 1.5})

    # Value labels
    for bar, m, vals in zip(bars, means, per_run):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.15,
                f'{m/1e6:.2f}M', ha='center', va='bottom',
                color='#e0e0e0', fontsize=11, fontweight='bold')
        # Individual run values
        run_str = ', '.join(f'{v/1e6:.2f}' for v in vals)
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() * 0.5,
                run_str, ha='center', va='center',
                color='#ffffff', fontsize=8, alpha=0.7)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel('Voluntary Context Switches (millions)', fontsize=12, color='#e0e0e0')
    ax.set_title('H4: Voluntary Context Switches — busy_poll Effect',
                 fontsize=14, color='#ffffff', fontweight='bold', pad=15)
    ax.tick_params(colors='#b0b0b0')
    ax.grid(axis='y', alpha=0.15, color='#ffffff')

    # Reference line for E4 baseline
    ax.axhline(y=means[0] / 1e6, color='#e74c3c', linestyle='--', alpha=0.4)

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h4_context_switches.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


def plot_full_overview(data_dir, output_dir):
    """
    Full overview CDF with all mitigation experiments (E5-E10, E14-E16) vs E4.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    experiments = {
        'E4 (baseline)':        ('E4',  '#e74c3c', '-',  2.5),
        'E5 (RPS→CPU0)':       ('E5',  '#9b59b6', '--', 1.5),
        'E6 (RPS→all)':        ('E6',  '#3498db', '--', 1.5),
        'E7 (app pinned)':     ('E7',  '#1abc9c', '--', 1.5),
        'E8 (RPS+pin)':        ('E8',  '#e67e22', '--', 1.5),
        'E9 (CFS lowlat)':     ('E9',  '#95a5a6', '--', 1.5),
        'E10 (ksoftirqd)':     ('E10', '#f1c40f', '--', 1.5),
        'E14 (combined)':      ('E14', '#2ecc71', '-.',  2.0),
        'E15 (RPS+bpoll)':     ('E15', '#00bcd4', '-.',  2.0),
        'E16 (bpoll only)':    ('E16', '#ff9800', '-.',  2.0),
    }

    for label, (exp, color, ls, lw) in experiments.items():
        try:
            x, y = load_experiment_cdf(data_dir, exp)
            ax.plot(x, y, label=label, color=color,
                    linestyle=ls, linewidth=lw)
        except Exception as e:
            print(f"  Warning: Could not load {exp}: {e}")

    ax.set_xscale('log')
    ax.set_xlim(1, 2e5)
    ax.set_ylim(0.9, 1.001)
    ax.set_xlabel('Run-queue Delay (μs)', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_ylabel('CDF', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_title('All Mitigations vs E4 Baseline — Scheduling Delay CDF',
                 fontsize=14, color='#ffffff', fontweight='bold', pad=15)

    ax.axhline(y=0.99, color='#ff6b6b', linestyle=':', alpha=0.5, linewidth=1)
    ax.text(2, 0.9905, 'p99', color='#ff6b6b', fontsize=9, alpha=0.6)

    ax.tick_params(colors='#b0b0b0')
    ax.grid(True, alpha=0.15, color='#ffffff')

    legend = ax.legend(loc='lower right', fontsize=9, ncol=2, framealpha=0.8,
                       facecolor='#1a1a2e', edgecolor='#444444')
    for text in legend.get_texts():
        text.set_color('#e0e0e0')

    plt.tight_layout()
    outpath = os.path.join(output_dir, '24_h4_full_overview_cdf.png')
    fig.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {outpath}")


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Validate H4: Combined Mitigations Hypothesis'
    )
    parser.add_argument('--data-dir', '-d', default='./data')
    parser.add_argument('--output-dir', '-o', default='./plots')
    args = parser.parse_args()

    print("=" * 60)
    print("  H4 Validation: Combined Mitigations Hypothesis")
    print("=" * 60)
    print()

    # ─── Percentile Analysis ─────────────────────────────────────

    experiments = ['E4', 'E5', 'E6', 'E7', 'E8', 'E9', 'E10', 'E14', 'E15', 'E16']
    exp_labels = {
        'E4':  'baseline (heavy+high)',
        'E5':  'RPS→CPU0',
        'E6':  'RPS→all CPUs',
        'E7':  'app pinned',
        'E8':  'RPS+app pinned',
        'E9':  'CFS lowlatency',
        'E10': 'ksoftirqd',
        'E14': 'RPS+pin+CFS',
        'E15': 'RPS+busy_poll',
        'E16': 'busy_poll only',
    }

    print("─── Scheduling Delay Percentiles ───")
    print(f"{'Exp':<6} {'Config':<22} {'p50':>8} {'p95':>8} {'p99':>8} {'p99.9':>10}")
    print("─" * 65)

    all_pcts = {}
    for exp in experiments:
        runs_dir = os.path.join(args.data_dir, exp)
        if not os.path.isdir(runs_dir):
            continue
        runs = sorted([d for d in os.listdir(runs_dir) if d.startswith('run_')])

        run_pcts = []
        for run in runs:
            fpath = os.path.join(runs_dir, run, 'sched_delay_summary.txt')
            pcts = get_percentiles_from_file(fpath)
            if pcts.get(99) is not None:
                run_pcts.append(pcts)

        if run_pcts:
            avg_pcts = {}
            for p in [50, 95, 99, 99.9]:
                vals = [d[p] for d in run_pcts if d.get(p) is not None]
                avg_pcts[p] = np.mean(vals) if vals else None
            all_pcts[exp] = avg_pcts

            print(f"{exp:<6} {exp_labels.get(exp, ''):<22} "
                  f"{_fmt_us(avg_pcts.get(50, 0)):>8} "
                  f"{_fmt_us(avg_pcts.get(95, 0)):>8} "
                  f"{_fmt_us(avg_pcts.get(99, 0)):>8} "
                  f"{_fmt_us(avg_pcts.get(99.9, 0)):>10}")

    # ─── Voluntary Context Switches ──────────────────────────────

    print()
    print("─── Voluntary Context Switches ───")
    print(f"{'Exp':<6} {'Config':<22} {'Run 1':>10} {'Run 2':>10} {'Run 3':>10} {'Avg':>10} {'vs E4':>8}")
    print("─" * 70)

    e4_avg = None
    all_vol = {}
    for exp in ['E4', 'E14', 'E15', 'E16']:
        vals = get_voluntary_switches_all_runs(args.data_dir, exp)
        if not vals:
            continue
        avg = np.mean(vals)
        all_vol[exp] = vals

        if exp == 'E4':
            e4_avg = avg
            pct_str = '  (ref)'
        else:
            pct_change = (avg - e4_avg) / e4_avg * 100 if e4_avg else 0
            pct_str = f'{pct_change:+.0f}%'

        run_strs = [f'{v/1e6:.2f}M' for v in vals]
        while len(run_strs) < 3:
            run_strs.append('—')
        print(f"{exp:<6} {exp_labels.get(exp, ''):<22} "
              f"{run_strs[0]:>10} {run_strs[1]:>10} {run_strs[2]:>10} "
              f"{avg/1e6:.2f}M{pct_str:>8}")

    # ─── Hypothesis Verdict ──────────────────────────────────────

    print()
    print("─── H4 Verdict ───")
    print()

    e4_p99 = all_pcts.get('E4', {}).get(99)
    has_improvement = False
    for exp in ['E14', 'E15', 'E16']:
        exp_p99 = all_pcts.get(exp, {}).get(99)
        if exp_p99 is not None and e4_p99 is not None:
            if exp_p99 < e4_p99 * 0.8:
                has_improvement = True
                print(f"  ✓ {exp} improved p99 by ≥20%: {_fmt_us(e4_p99)} → {_fmt_us(exp_p99)}")
            else:
                ratio = exp_p99 / e4_p99 if e4_p99 > 0 else float('inf')
                print(f"  ✗ {exp} p99 = {_fmt_us(exp_p99)} ({ratio:.1f}× baseline) — no improvement")

    print()
    if has_improvement:
        print("  RESULT: H4 SUPPORTED — at least one mitigation reduced p99 by ≥20%")
    else:
        print("  RESULT: H4 NOT SUPPORTED — no mitigation reduced p99 latency")
        print()
        # Check for context switch improvement
        e15_avg = np.mean(all_vol.get('E15', [0]))
        e16_avg = np.mean(all_vol.get('E16', [0]))
        if e4_avg and (e15_avg < e4_avg * 0.5 or e16_avg < e4_avg * 0.5):
            pct = (1 - min(e15_avg, e16_avg) / e4_avg) * 100
            print(f"  HOWEVER: SO_BUSY_POLL reduced voluntary context switches by {pct:.0f}%")
            print(f"    E4 avg:  {e4_avg/1e6:.2f}M switches")
            print(f"    E15 avg: {e15_avg/1e6:.2f}M switches")
            print(f"    E16 avg: {e16_avg/1e6:.2f}M switches")
            print(f"    E14 avg: {np.mean(all_vol.get('E14', [0]))/1e6:.2f}M (no busy_poll → similar to E4)")
            print()
            print("  → Bottleneck is CPU contention with stress-ng, not context switch overhead")
            print("  → busy_poll reduces scheduling overhead but cannot fix CPU starvation")

    # ─── Generate Plots ──────────────────────────────────────────

    print()
    print("─── Generating Plots ───")
    os.makedirs(args.output_dir, exist_ok=True)

    plot_mitigation_cdf(args.data_dir, args.output_dir)
    plot_context_switches(args.data_dir, args.output_dir)
    plot_full_overview(args.data_dir, args.output_dir)

    print()
    print("=" * 60)
    print("  H4 Validation Complete")
    print("=" * 60)


if __name__ == '__main__':
    main()

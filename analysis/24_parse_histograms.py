#!/usr/bin/env python3
"""
24_parse_histograms.py — Parse bpftrace histograms and generate CDF plots.

Usage:
    python3 analysis/24_parse_histograms.py --experiments E1 E2 E3 E4 \
        --data-dir ./data --output ./plots/24_cdf_runq_delay_baselines.png

This script:
  1. Reads the *last* @runq_delay_us histogram from each experiment's sched_delay_summary.txt
  2. Averages across runs (run_1, run_2, run_3)
  3. Generates an overlaid CDF plot
"""

import argparse
import re
import os
import sys
import numpy as np

# Try matplotlib — fail gracefully with instructions
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError:
    print("ERROR: matplotlib not found. Install with: pip3 install matplotlib")
    sys.exit(1)


# ─── Histogram Parsing ──────────────────────────────────────────

def parse_last_histogram(filepath, hist_name="@runq_delay_us"):
    """
    Extract the LAST occurrence of a named bpftrace histogram from a summary file.
    Returns list of (bucket_low_us, bucket_high_us, count) tuples.
    """
    if not os.path.exists(filepath):
        print(f"  WARNING: {filepath} not found")
        return []

    with open(filepath, 'r') as f:
        content = f.read()

    # Find ALL occurrences of the histogram
    # Pattern: @hist_name:\n followed by bucket lines until empty line
    pattern = re.escape(hist_name) + r':\s*\n((?:\[.*\n)*)'
    matches = list(re.finditer(pattern, content))

    if not matches:
        print(f"  WARNING: No '{hist_name}' found in {filepath}")
        return []

    # Use the LAST match (cumulative histogram)
    last_match = matches[-1]
    hist_text = last_match.group(1)

    buckets = []
    for line in hist_text.strip().split('\n'):
        line = line.strip()
        if not line.startswith('['):
            continue

        # Parse bucket formats:
        #   [0]           274036 |@@@@...|
        #   [2, 4)         61903 |@@@@...|
        #   [512K, 1M)        33 |...|
        m = re.match(
            r'\[([0-9KMG]+)(?:,\s*([0-9KMG]+)\))?\s+(\d+)\s+\|',
            line
        )
        if m:
            low = _parse_suffix(m.group(1))
            if m.group(2):
                high = _parse_suffix(m.group(2))
            else:
                # Single-value bucket like [0] or [1]
                high = low + 1
            count = int(m.group(3))
            buckets.append((low, high, count))

    return buckets


def _parse_suffix(s):
    """Parse values like '512K', '1M', '2G' into integers."""
    s = s.strip()
    multipliers = {'K': 1000, 'M': 1_000_000, 'G': 1_000_000_000}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return int(s[:-1]) * mult
    return int(s)


def buckets_to_cdf(buckets):
    """
    Convert histogram buckets to CDF arrays.
    Returns (x_values_us, cdf_values) where CDF is 0..1.
    Uses the upper edge of each bucket for the x-axis.
    """
    if not buckets:
        return np.array([]), np.array([])

    total = sum(count for _, _, count in buckets)
    if total == 0:
        return np.array([]), np.array([])

    # Sort by bucket lower bound
    buckets.sort(key=lambda b: b[0])

    x = []
    cumulative = []
    running = 0

    for low, high, count in buckets:
        running += count
        x.append(high)  # Upper edge of bucket
        cumulative.append(running / total)

    return np.array(x, dtype=float), np.array(cumulative, dtype=float)


def load_experiment_cdf(data_dir, experiment, hist_name="@runq_delay_us"):
    """
    Load histogram data for an experiment, averaging across all runs.
    Returns (x_us, cdf) arrays.
    """
    exp_dir = os.path.join(data_dir, experiment)
    if not os.path.isdir(exp_dir):
        print(f"  ERROR: Experiment directory {exp_dir} not found")
        return np.array([]), np.array([])

    # Find all runs
    runs = sorted([d for d in os.listdir(exp_dir) if d.startswith('run_')])
    if not runs:
        print(f"  ERROR: No runs found in {exp_dir}")
        return np.array([]), np.array([])

    # Collect all bucket data across runs, then average
    all_buckets = {}  # {(low, high): [counts across runs]}

    for run in runs:
        summary_file = os.path.join(exp_dir, run, 'sched_delay_summary.txt')
        buckets = parse_last_histogram(summary_file, hist_name)
        for low, high, count in buckets:
            key = (low, high)
            if key not in all_buckets:
                all_buckets[key] = []
            all_buckets[key].append(count)

    if not all_buckets:
        return np.array([]), np.array([])

    # Average counts across runs
    avg_buckets = []
    for (low, high), counts in all_buckets.items():
        avg_count = np.mean(counts)
        avg_buckets.append((low, high, avg_count))

    return buckets_to_cdf(avg_buckets)


# ─── Plotting ────────────────────────────────────────────────────

# Experiment metadata for labels and colors
EXP_META = {
    'E1':  {'label': 'E1: No stress, Low load',        'color': '#2ecc71', 'ls': '-'},
    'E2':  {'label': 'E2: No stress, High load',       'color': '#3498db', 'ls': '-'},
    'E3':  {'label': 'E3: Heavy stress, Low load',     'color': '#f39c12', 'ls': '-'},
    'E4':  {'label': 'E4: Heavy stress, High load',    'color': '#e74c3c', 'ls': '-'},
    'E5':  {'label': 'E5: RPS→CPU0',                   'color': '#9b59b6', 'ls': '--'},
    'E6':  {'label': 'E6: RPS→all CPUs',               'color': '#1abc9c', 'ls': '--'},
    'E7':  {'label': 'E7: App pinned',                 'color': '#e67e22', 'ls': '--'},
    'E8':  {'label': 'E8: RPS+App pinned',             'color': '#34495e', 'ls': '--'},
    'E9':  {'label': 'E9: CFS lowlatency',             'color': '#e91e63', 'ls': '-.'},
    'E10': {'label': 'E10: Forced ksoftirqd',          'color': '#00bcd4', 'ls': '-.'},
    'E11': {'label': 'E11: UDP, no stress',            'color': '#8bc34a', 'ls': '-.'},
    'E12': {'label': 'E12: UDP + heavy stress',        'color': '#ff5722', 'ls': '-.'},
    'E13': {'label': 'E13: Moderate CPU stress',       'color': '#795548', 'ls': '-.'},
    'E14': {'label': 'E14: Combined mitigations',      'color': '#607d8b', 'ls': ':'},
    'E15': {'label': 'E15: RPS+busy_poll',             'color': '#673ab7', 'ls': ':'},
    'E16': {'label': 'E16: busy_poll only',            'color': '#ff9800', 'ls': ':'},
}


def plot_cdf(experiments, data_dir, output_path, title=None, log_x=True):
    """
    Generate an overlaid CDF plot for multiple experiments.
    """
    # Premium dark theme
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 7))

    # Set background
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    for exp in experiments:
        meta = EXP_META.get(exp, {'label': exp, 'color': '#ffffff', 'ls': '-'})
        x, cdf = load_experiment_cdf(data_dir, exp)

        if len(x) == 0:
            print(f"  Skipping {exp}: no data")
            continue

        ax.plot(x, cdf,
                label=meta['label'],
                color=meta['color'],
                linestyle=meta['ls'],
                linewidth=2.5,
                alpha=0.9)

        # Find and annotate p99
        idx_p99 = np.searchsorted(cdf, 0.99)
        if idx_p99 < len(x):
            p99_val = x[idx_p99]
            ax.axvline(x=p99_val, color=meta['color'], alpha=0.3, linestyle=':', linewidth=1)

    # Horizontal reference lines
    for p, label in [(0.5, 'p50'), (0.95, 'p95'), (0.99, 'p99')]:
        ax.axhline(y=p, color='#ffffff', alpha=0.15, linestyle='--', linewidth=0.8)
        ax.text(ax.get_xlim()[0] if ax.get_xlim()[0] > 0 else 0.5,
                p + 0.01, label,
                color='#ffffff', alpha=0.4, fontsize=9)

    # Formatting
    if log_x:
        ax.set_xscale('log')

    ax.set_xlabel('Runqueue Delay (μs)', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_ylabel('CDF (Cumulative Fraction)', fontsize=13, color='#e0e0e0', fontweight='bold')

    if title is None:
        title = f'Runqueue Delay CDF — {", ".join(experiments)}'
    ax.set_title(title, fontsize=15, color='#ffffff', fontweight='bold', pad=15)

    ax.set_ylim(0, 1.02)
    ax.set_xlim(left=0.8)

    # Grid
    ax.grid(True, alpha=0.15, color='#ffffff')
    ax.tick_params(colors='#b0b0b0', labelsize=11)

    # Legend
    legend = ax.legend(loc='lower right', fontsize=10, framealpha=0.8,
                       facecolor='#1a1a2e', edgecolor='#444444')
    for text in legend.get_texts():
        text.set_color('#e0e0e0')

    # Add percentile summary table
    _add_percentile_table(ax, experiments, data_dir)

    plt.tight_layout()

    # Save
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"\n  ✓ CDF plot saved to: {output_path}")


def _add_percentile_table(ax, experiments, data_dir):
    """Add a small text box with p50/p95/p99 values."""
    lines = ["  Exp    p50     p95      p99"]
    lines.append("  " + "─" * 32)

    for exp in experiments:
        x, cdf = load_experiment_cdf(data_dir, exp)
        if len(x) == 0:
            continue

        p50 = x[np.searchsorted(cdf, 0.50)] if np.any(cdf >= 0.50) else float('nan')
        p95 = x[np.searchsorted(cdf, 0.95)] if np.any(cdf >= 0.95) else float('nan')
        p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')

        lines.append(f"  {exp:4s}  {_fmt_us(p50):>7s}  {_fmt_us(p95):>7s}  {_fmt_us(p99):>7s}")

    text = '\n'.join(lines)
    ax.text(0.02, 0.55, text, transform=ax.transAxes,
            fontsize=8.5, fontfamily='monospace',
            color='#c0c0c0', alpha=0.9,
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#1a1a2e',
                      edgecolor='#444444', alpha=0.85),
            verticalalignment='top')


def _fmt_us(val):
    """Format microsecond values for display."""
    if np.isnan(val):
        return "n/a"
    if val < 1000:
        return f"{val:.0f}μs"
    elif val < 1_000_000:
        return f"{val/1000:.1f}ms"
    else:
        return f"{val/1_000_000:.1f}s"


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Parse bpftrace histograms and generate CDF plots')
    parser.add_argument('--experiments', '-e', nargs='+', required=True,
                        help='Experiment names to plot (e.g., E1 E2 E3 E4)')
    parser.add_argument('--data-dir', '-d', default='./data',
                        help='Root data directory (default: ./data)')
    parser.add_argument('--output', '-o', default='./plots/cdf_runq_delay.png',
                        help='Output plot path (default: ./plots/cdf_runq_delay.png)')
    parser.add_argument('--title', '-t', default=None,
                        help='Custom plot title')
    parser.add_argument('--linear', action='store_true',
                        help='Use linear x-axis instead of log scale')
    parser.add_argument('--print-percentiles', action='store_true',
                        help='Print percentile table to stdout')

    args = parser.parse_args()

    print(f"Parsing histograms for: {', '.join(args.experiments)}")
    print(f"Data directory: {args.data_dir}")

    if args.print_percentiles:
        print(f"\n{'Experiment':>10s}  {'p50':>8s}  {'p95':>8s}  {'p99':>8s}  {'p99.9':>8s}")
        print("  " + "─" * 44)
        for exp in args.experiments:
            x, cdf = load_experiment_cdf(args.data_dir, exp)
            if len(x) == 0:
                print(f"  {exp:>8s}  {'n/a':>8s}  {'n/a':>8s}  {'n/a':>8s}  {'n/a':>8s}")
                continue
            p50 = x[np.searchsorted(cdf, 0.50)] if np.any(cdf >= 0.50) else float('nan')
            p95 = x[np.searchsorted(cdf, 0.95)] if np.any(cdf >= 0.95) else float('nan')
            p99 = x[np.searchsorted(cdf, 0.99)] if np.any(cdf >= 0.99) else float('nan')
            p999 = x[np.searchsorted(cdf, 0.999)] if np.any(cdf >= 0.999) else float('nan')
            print(f"  {exp:>8s}  {_fmt_us(p50):>8s}  {_fmt_us(p95):>8s}  {_fmt_us(p99):>8s}  {_fmt_us(p999):>8s}")
        print()

    plot_cdf(args.experiments, args.data_dir, args.output,
             title=args.title, log_x=not args.linear)


if __name__ == '__main__':
    main()

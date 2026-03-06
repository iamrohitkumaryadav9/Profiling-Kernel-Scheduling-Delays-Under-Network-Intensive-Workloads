#!/usr/bin/env python3
"""
24_validate_h1.py — Validate H1: Softirq Colocation Hypothesis

Analyzes per-CPU softirq distribution across E4 (default), E5 (RPS pinned),
E6 (RPS spread), E7 (app pinned), E8 (RPS pinned + app pinned).

Outputs:
  1. Per-CPU softirq distribution bar charts (E4 vs E5, E4 vs E6, E8)
  2. Softirq concentration metrics (Gini coefficient, max fraction)
  3. Console summary table

Usage:
    python3 analysis/24_validate_h1.py --data-dir ./data --output-dir ./plots
"""

import argparse
import re
import os
import sys
import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError:
    print("ERROR: matplotlib not found. Install with: pip3 install matplotlib")
    sys.exit(1)


# ─── Data Extraction ─────────────────────────────────────────────

def extract_last_per_cpu_map(filepath, map_name):
    """
    Extract the LAST occurrence of a bpftrace per-CPU map like @net_rx_count[cpu]: value.
    Returns dict {cpu_id: value}.
    """
    if not os.path.exists(filepath):
        return {}

    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Find all blocks — a block is a contiguous sequence of @map_name[N]: value lines
    pattern = re.compile(rf'^{re.escape(map_name)}\[(\d+)\]:\s+(\d+)')

    blocks = []
    current_block = {}

    for line in lines:
        m = pattern.match(line.strip())
        if m:
            cpu = int(m.group(1))
            val = int(m.group(2))
            current_block[cpu] = val
        else:
            if current_block:
                blocks.append(current_block)
                current_block = {}

    if current_block:
        blocks.append(current_block)

    if not blocks:
        return {}

    # Return LAST block (cumulative)
    return blocks[-1]


def load_experiment_softirq(data_dir, experiment, num_cpus=20):
    """
    Load per-CPU softirq data for an experiment, averaging across runs.
    Returns dict of metric_name -> np.array(shape=(num_cpus,))
    """
    exp_dir = os.path.join(data_dir, experiment)
    if not os.path.isdir(exp_dir):
        print(f"  ERROR: {exp_dir} not found")
        return {}

    runs = sorted([d for d in os.listdir(exp_dir) if d.startswith('run_')])
    if not runs:
        print(f"  ERROR: No runs in {exp_dir}")
        return {}

    metrics = {
        'net_rx_count': [],    # Softirq invocation count
        'pkt_recv': [],        # Packets received
        'net_rx_total_us': [], # Total softirq CPU time (microseconds)
    }

    for run in runs:
        summary = os.path.join(exp_dir, run, 'softirq_net_summary.txt')
        for metric_name in metrics:
            data = extract_last_per_cpu_map(summary, f'@{metric_name}')
            if data:
                arr = np.zeros(num_cpus)
                for cpu, val in data.items():
                    if cpu < num_cpus:
                        arr[cpu] = val
                metrics[metric_name].append(arr)

    # Average across runs
    result = {}
    for metric_name, arrays in metrics.items():
        if arrays:
            result[metric_name] = np.mean(arrays, axis=0)
        else:
            result[metric_name] = np.zeros(num_cpus)

    return result


def compute_concentration_metrics(per_cpu_values):
    """
    Compute concentration metrics for a per-CPU distribution.
    Returns dict with gini, max_fraction, top3_fraction, cv (coefficient of variation).
    """
    total = np.sum(per_cpu_values)
    if total == 0:
        return {'gini': 0, 'max_fraction': 0, 'top3_fraction': 0, 'cv': 0, 'total': 0}

    fractions = per_cpu_values / total
    sorted_fracs = np.sort(fractions)

    # Gini coefficient
    n = len(sorted_fracs)
    index = np.arange(1, n + 1)
    gini = (2 * np.sum(index * sorted_fracs)) / (n * np.sum(sorted_fracs)) - (n + 1) / n

    # Other metrics
    max_frac = np.max(fractions)
    top3_frac = np.sum(np.sort(fractions)[-3:])
    cv = np.std(fractions) / np.mean(fractions) if np.mean(fractions) > 0 else 0

    return {
        'gini': gini,
        'max_fraction': max_frac,
        'top3_fraction': top3_frac,
        'cv': cv,
        'total': total,
    }


# ─── Plotting ────────────────────────────────────────────────────

def plot_per_cpu_comparison(experiments_data, metric, output_path, title, num_cpus=20):
    """
    Generate grouped bar chart comparing per-CPU values across experiments.
    """
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    cpus = np.arange(num_cpus)
    n_exp = len(experiments_data)
    bar_width = 0.8 / n_exp

    colors = ['#e74c3c', '#9b59b6', '#1abc9c', '#e67e22', '#34495e',
              '#3498db', '#2ecc71', '#f39c12']

    for i, (exp_name, data) in enumerate(experiments_data.items()):
        values = data.get(metric, np.zeros(num_cpus))
        # Normalize to fraction of total
        total = np.sum(values)
        if total > 0:
            fracs = values / total * 100  # Percentage
        else:
            fracs = np.zeros(num_cpus)

        offset = (i - n_exp / 2 + 0.5) * bar_width
        bars = ax.bar(cpus + offset, fracs, bar_width * 0.9,
                      label=exp_name, color=colors[i % len(colors)],
                      alpha=0.85, edgecolor='none')

    ax.set_xlabel('CPU Core', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_ylabel('% of Total', fontsize=13, color='#e0e0e0', fontweight='bold')
    ax.set_title(title, fontsize=14, color='#ffffff', fontweight='bold', pad=15)
    ax.set_xticks(cpus)
    ax.set_xticklabels([str(c) for c in cpus], fontsize=9)
    ax.tick_params(colors='#b0b0b0')
    ax.grid(axis='y', alpha=0.15, color='#ffffff')

    legend = ax.legend(loc='upper right', fontsize=10, framealpha=0.8,
                       facecolor='#1a1a2e', edgecolor='#444444')
    for text in legend.get_texts():
        text.set_color('#e0e0e0')

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {output_path}")


def plot_concentration_summary(all_metrics, output_path):
    """
    Generate a bar chart comparing Gini coefficient and max-CPU fraction across experiments.
    """
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.patch.set_facecolor('#1a1a2e')
    for ax in (ax1, ax2):
        ax.set_facecolor('#16213e')

    exps = list(all_metrics.keys())
    ginis = [all_metrics[e]['gini'] for e in exps]
    max_fracs = [all_metrics[e]['max_fraction'] * 100 for e in exps]

    colors = ['#e74c3c', '#9b59b6', '#1abc9c', '#e67e22', '#34495e']

    # Gini coefficient
    bars1 = ax1.bar(exps, ginis, color=colors[:len(exps)], alpha=0.85, edgecolor='none')
    ax1.set_ylabel('Gini Coefficient', fontsize=12, color='#e0e0e0')
    ax1.set_title('Softirq Concentration (Gini)', fontsize=13, color='#ffffff', fontweight='bold')
    ax1.set_ylim(0, 1)
    ax1.tick_params(colors='#b0b0b0')
    ax1.grid(axis='y', alpha=0.15, color='#ffffff')
    for bar, val in zip(bars1, ginis):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                 f'{val:.2f}', ha='center', va='bottom', color='#e0e0e0', fontsize=11)

    # Max CPU fraction
    bars2 = ax2.bar(exps, max_fracs, color=colors[:len(exps)], alpha=0.85, edgecolor='none')
    ax2.set_ylabel('Max Single-CPU Share (%)', fontsize=12, color='#e0e0e0')
    ax2.set_title('Hottest CPU Softirq Share', fontsize=13, color='#ffffff', fontweight='bold')
    ax2.set_ylim(0, 100)
    ax2.tick_params(colors='#b0b0b0')
    ax2.grid(axis='y', alpha=0.15, color='#ffffff')
    for bar, val in zip(bars2, max_fracs):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                 f'{val:.1f}%', ha='center', va='bottom', color='#e0e0e0', fontsize=11)

    # Equal spread reference line (1/N_CPUS)
    ax2.axhline(y=5, color='#2ecc71', alpha=0.5, linestyle='--', linewidth=1)
    ax2.text(len(exps) - 0.7, 5.5, 'ideal (5%)', color='#2ecc71', alpha=0.6, fontsize=9)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    print(f"  ✓ Saved: {output_path}")


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Validate H1: Softirq Colocation Hypothesis')
    parser.add_argument('--data-dir', '-d', default='./data')
    parser.add_argument('--output-dir', '-o', default='./plots')
    parser.add_argument('--num-cpus', type=int, default=20)
    args = parser.parse_args()

    num_cpus = args.num_cpus

    print("=" * 60)
    print("  H1 Validation: Softirq Colocation Hypothesis")
    print("=" * 60)
    print()

    # Load data for relevant experiments
    experiments = {
        'E4 (default)': 'E4',
        'E5 (RPS→CPU0)': 'E5',
        'E6 (RPS→all)': 'E6',
        'E7 (app pin)': 'E7',
        'E8 (RPS+pin)': 'E8',
    }

    all_data = {}
    for label, exp in experiments.items():
        print(f"Loading {label} ({exp})...")
        all_data[label] = load_experiment_softirq(args.data_dir, exp, num_cpus)

    print()

    # ─── Analysis 1: Per-CPU softirq distribution ────────────────

    print("─── Per-CPU NET_RX Softirq Distribution ───")
    print(f"{'Experiment':<20s}", end="")
    for cpu in range(num_cpus):
        print(f" CPU{cpu:02d}", end="")
    print("   Total")
    print("─" * (20 + num_cpus * 6 + 10))

    all_conc_metrics = {}
    for label, data in all_data.items():
        rx_counts = data.get('net_rx_count', np.zeros(num_cpus))
        total = np.sum(rx_counts)
        print(f"{label:<20s}", end="")
        for cpu in range(num_cpus):
            frac = rx_counts[cpu] / total * 100 if total > 0 else 0
            if frac > 10:
                print(f" {frac:4.0f}%", end="")
            elif frac > 1:
                print(f" {frac:4.1f}", end="")
            else:
                print(f"    . ", end="")
        print(f"  {total/1e6:.1f}M")

        all_conc_metrics[label] = compute_concentration_metrics(rx_counts)

    # ─── Analysis 2: Concentration metrics ───────────────────────

    print()
    print("─── Softirq Concentration Metrics ───")
    print(f"{'Experiment':<20s} {'Gini':>6s} {'Max CPU%':>9s} {'Top-3 CPUs%':>12s} {'CV':>6s}")
    print("─" * 55)
    for label, metrics in all_conc_metrics.items():
        print(f"{label:<20s} {metrics['gini']:6.3f} {metrics['max_fraction']*100:8.1f}% "
              f"{metrics['top3_fraction']*100:10.1f}%  {metrics['cv']:6.2f}")

    # ─── Analysis 3: H1 specific comparisons ─────────────────────

    print()
    print("─── H1 Hypothesis Checks ───")
    print()

    # Check 1: E4 vs E5 — RPS pinning shifts softirq to CPU0
    e4_data = all_data.get('E4 (default)', {})
    e5_data = all_data.get('E5 (RPS→CPU0)', {})
    if e4_data and e5_data:
        e4_rx = e4_data.get('net_rx_count', np.zeros(num_cpus))
        e5_rx = e5_data.get('net_rx_count', np.zeros(num_cpus))
        e4_cpu0_frac = e4_rx[0] / np.sum(e4_rx) * 100 if np.sum(e4_rx) > 0 else 0
        e5_cpu0_frac = e5_rx[0] / np.sum(e5_rx) * 100 if np.sum(e5_rx) > 0 else 0
        e4_max_cpu = np.argmax(e4_rx)
        e5_max_cpu = np.argmax(e5_rx)
        print(f"  ✓ E4 vs E5 (RPS pinning to CPU0):")
        print(f"    E4: CPU0 handles {e4_cpu0_frac:.1f}% of softirqs, max is CPU{e4_max_cpu} ({e4_rx[e4_max_cpu]/np.sum(e4_rx)*100:.1f}%)")
        print(f"    E5: CPU0 handles {e5_cpu0_frac:.1f}% of softirqs, max is CPU{e5_max_cpu} ({e5_rx[e5_max_cpu]/np.sum(e5_rx)*100:.1f}%)")
        if e5_cpu0_frac > e4_cpu0_frac:
            print(f"    → RPS pinning increased CPU0 softirq share: {e4_cpu0_frac:.1f}% → {e5_cpu0_frac:.1f}% ✓")
        else:
            print(f"    → RPS pinning did NOT increase CPU0 share (may reflect namespace behavior)")

    # Check 2: E4 vs E6 — RPS spread reduces max-CPU fraction
    e6_data = all_data.get('E6 (RPS→all)', {})
    if e4_data and e6_data:
        e4_conc = all_conc_metrics.get('E4 (default)', {})
        e6_conc = all_conc_metrics.get('E6 (RPS→all)', {})
        print(f"\n  ✓ E4 vs E6 (RPS spread):")
        print(f"    E4 max-CPU fraction: {e4_conc['max_fraction']*100:.1f}%, Gini: {e4_conc['gini']:.3f}")
        print(f"    E6 max-CPU fraction: {e6_conc['max_fraction']*100:.1f}%, Gini: {e6_conc['gini']:.3f}")
        if e6_conc['max_fraction'] < e4_conc['max_fraction']:
            print(f"    → RPS spread REDUCED max-CPU softirq share ✓")
        else:
            print(f"    → RPS spread did NOT reduce max-CPU share (kernel may already balance veth)")

    # Check 3: E8 — app on CPUs 2,3 should show isolation
    e8_data = all_data.get('E8 (RPS+pin)', {})
    if e8_data:
        e8_rx = e8_data.get('net_rx_count', np.zeros(num_cpus))
        total_e8 = np.sum(e8_rx)
        app_cpus = [2, 3]
        app_cpu_frac = sum(e8_rx[c] for c in app_cpus) / total_e8 * 100 if total_e8 > 0 else 0
        other_frac = 100 - app_cpu_frac
        print(f"\n  ✓ E8 (RPS pinned + app on CPUs 2,3):")
        print(f"    CPUs 2,3 (app threads) handle {app_cpu_frac:.1f}% of softirqs")
        print(f"    Other CPUs handle {other_frac:.1f}% of softirqs")
        if app_cpu_frac < 20:
            print(f"    → Good isolation: app CPUs handle minimal softirq work ✓")
        else:
            print(f"    → Note: app CPUs still handle significant softirq ({app_cpu_frac:.1f}%)")
            print(f"      (RPS pin may steer TO CPU0, but kernel can also schedule softirq on 2,3)")

    # ─── Generate plots ──────────────────────────────────────────

    print()
    print("─── Generating Plots ───")

    # Plot 1: E4 vs E5 per-CPU comparison
    plot_per_cpu_comparison(
        {'E4 (default)': all_data['E4 (default)'],
         'E5 (RPS→CPU0)': all_data['E5 (RPS→CPU0)']},
        'pkt_recv', 
        os.path.join(args.output_dir, '24_h1_e4_vs_e5_percpu.png'),
        'H1: E4 vs E5 — Per-CPU Packet Distribution (RPS Pinning Effect)',
        num_cpus
    )

    # Plot 2: E4 vs E6 per-CPU comparison
    plot_per_cpu_comparison(
        {'E4 (default)': all_data['E4 (default)'],
         'E6 (RPS→all)': all_data['E6 (RPS→all)']},
        'pkt_recv',
        os.path.join(args.output_dir, '24_h1_e4_vs_e6_percpu.png'),
        'H1: E4 vs E6 — Per-CPU Packet Distribution (RPS Spread Effect)',
        num_cpus
    )

    # Plot 3: E8 — isolation check
    plot_per_cpu_comparison(
        {'E4 (default)': all_data['E4 (default)'],
         'E8 (RPS+pin)': all_data['E8 (RPS+pin)']},
        'pkt_recv',
        os.path.join(args.output_dir, '24_h1_e8_isolation.png'),
        'H1: E4 vs E8 — Per-CPU Distribution (RPS Pinning + App Pinning)',
        num_cpus
    )

    # Plot 4: All experiments per-CPU overview
    plot_per_cpu_comparison(
        {k: v for k, v in all_data.items()},
        'pkt_recv',
        os.path.join(args.output_dir, '24_h1_all_percpu_overview.png'),
        'H1 Overview: Per-CPU Packet Distribution Across All Placement Configs',
        num_cpus
    )

    # Plot 5: Concentration metrics comparison
    plot_concentration_summary(
        all_conc_metrics,
        os.path.join(args.output_dir, '24_h1_concentration_metrics.png')
    )

    print()
    print("=" * 60)
    print("  H1 Validation Complete")
    print("=" * 60)


if __name__ == '__main__':
    main()

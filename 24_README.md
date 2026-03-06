# Profiling Kernel Scheduling Delays Under Network-Intensive Workloads

An eBPF-based analysis and mitigation framework for quantifying and reducing scheduling delays caused by softirq processing under network load.

## Overview

This project uses eBPF to instrument the Linux scheduler, softirq, and network subsystems. It runs 16 controlled experiments (48 total runs) varying CPU contention, network load, softirq CPU placement, CFS tuning, and protocol to validate four hypotheses about scheduling delay behavior.

**Key Findings:**
- CPU contention causes a 31× increase in p99 scheduling delay (64μs → 2ms)
- Softirq follows socket affinity on veth (Gini=0.81)
- UDP is 13× worse than TCP at p99 under stress (no flow control)
- `SO_BUSY_POLL` reduces context switches by 68% but cannot fix CPU starvation

## System Requirements

| Component | Version |
|---|---|
| Ubuntu | 22.04 LTS or 24.04 LTS |
| Kernel | ≥ 5.15 with BTF (`CONFIG_DEBUG_INFO_BTF=y`) |
| bpftrace | ≥ 0.17.0 |
| bcc-tools | ≥ 0.25.0 |
| iperf3 | ≥ 3.9 |
| memcached | ≥ 1.6 |
| libmemcached-tools | ≥ 1.0 |
| stress-ng | ≥ 0.13 |
| Python 3 | ≥ 3.9 (matplotlib, numpy) |
| CPU cores | ≥ 4 (recommended 8+) |

## Quick Start

```bash
# 1. Install dependencies
sudo apt update && sudo apt install -y \
    bpftrace bpfcc-tools linux-tools-$(uname -r) \
    iperf3 memcached libmemcached-tools stress-ng \
    python3-matplotlib python3-numpy

# 2. Set up testbed (network namespaces + veth pair)
sudo scripts/24_setup_testbed.sh setup

# 3. Run all 16 experiments (3 runs each, ~4 hours total)
sudo scripts/24_run_all_experiments.sh

# 4. Generate analysis plots
python3 analysis/24_generate_plots.py

# 5. Validate individual hypotheses
python3 analysis/24_validate_h1.py
python3 analysis/24_validate_h2_h3.py
python3 analysis/24_validate_h4.py

# 6. Compile report
cd report && make
```

## Directory Structure

```
MT25037/
├── scripts/                    # Experiment orchestration
│   ├── 24_setup_testbed.sh        # Create/destroy network namespaces
│   ├── 24_run_experiment.sh       # Run a single experiment
│   └── 24_run_all_experiments.sh  # Run all 16 experiments
├── ebpf_tools/                 # eBPF instrumentation scripts
│   ├── 24_sched_delay.bt          # Runqueue latency profiler
│   ├── 24_softirq_net.bt          # Softirq duration & CPU%
│   ├── 24_net_drops.bt            # Packet drops & TCP retransmits
│   ├── 24_cpu_migrations.bt       # Task CPU migration tracker
│   ├── 24_proc_pollers.sh         # /proc metrics collector (5 pollers)
│   └── 24_busy_poll_echo_server.c # SO_BUSY_POLL echo server for E15/E16
├── analysis/                   # Analysis & visualization
│   ├── 24_parse_histograms.py     # Shared histogram parser & CDF
│   ├── 24_validate_h1.py          # H1: softirq colocation
│   ├── 24_validate_h2_h3.py       # H2: ksoftirqd + H3: TCP vs UDP
│   ├── 24_validate_h4.py          # H4: combined mitigations
│   └── 24_generate_plots.py       # Phase 7: all derived metrics + plots
├── data/                       # Raw experiment data (48 runs)
│   └── E{1..16}/run_{1..3}/    # 20 files per run
├── plots/                      # Generated plots (33 total)
│   └── 24_experiment_metrics.csv  # Derived metrics for all experiments
├── report/                     # LaTeX technical report
│   ├── 24_report.tex
│   ├── 24_references.bib
│   └── 24_Makefile
├── 24_PROJECT_BLUEPRINT.md        # Detailed project design document
└── 24_PROJECT_STATUS.md           # Phase completion tracker
```

## Experiment Matrix

| Exp | CPU | Net | RPS | App Pin | CFS | Protocol | Purpose |
|---|---|---|---|---|---|---|---|
| E1 | None | Low | Default | None | Default | TCP | Baseline |
| E2 | None | High | Default | None | Default | TCP | Net load only |
| E3 | Heavy | Low | Default | None | Default | TCP | CPU stress only |
| E4 | Heavy | High | Default | None | Default | TCP | Worst case |
| E5 | Heavy | High | CPU 0 | None | Default | TCP | RPS pinned |
| E6 | Heavy | High | All | None | Default | TCP | RPS spread |
| E7 | Heavy | High | Default | 2,3 | Default | TCP | App pinned |
| E8 | Heavy | High | CPU 0 | 2,3 | Default | TCP | RPS + pin |
| E9 | Heavy | High | Default | None | Lowlat | TCP | CFS tuning |
| E10 | Heavy | High | Default | None | Default | TCP | ksoftirqd |
| E11 | None | High | Default | None | Default | UDP | UDP baseline |
| E12 | Heavy | High | Default | None | Default | UDP | UDP + stress |
| E13 | Moderate | High | Default | None | Default | TCP | Threshold |
| E14 | Heavy | High | All | 2,3 | Lowlat | TCP | Combined |
| E15 | Heavy | High | All | None | Default | TCP | RPS+bpoll |
| E16 | Heavy | High | Default | None | Default | TCP | bpoll only |

## Teardown

```bash
sudo scripts/24_setup_testbed.sh teardown
```

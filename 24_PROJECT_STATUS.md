# Project Status Tracker

> **Last updated:** 2026-03-06  
> **Project:** Profiling Kernel Scheduling Delays Under Network-Intensive Workloads  
> **Reference:** [24_PROJECT_BLUEPRINT.md](./24_PROJECT_BLUEPRINT.md)

Use this checklist to track progress and pick up from where it was left. Mark items `[x]` when complete, `[/]` when in-progress, `[ ]` when not started.

---

## Phase 1: Environment Setup (Week 1)

### 1.1 System Requirements
- [x] Ubuntu 22.04.5 LTS installed
- [x] Kernel 6.8.0-94-generic with BTF support verified
- [x] 20 CPU cores available

### 1.2 Tool Installation
- [x] `bpftrace` v0.21.3 installed at `/usr/local/bin/bpftrace` (upgraded from broken 0.14)
- [x] `bcc-tools` 0.18.0 installed
- [x] `iperf3` 3.9 installed
- [x] `stress-ng` 0.13.12 installed
- [x] `perf` 6.8.12 installed
- [x] `memcached` 1.6.14 installed
- [x] `libmemcached-tools` 1.0.18 installed
- [x] Python 3.10.12 with matplotlib 3.10.8, pandas 2.3.2, numpy 2.2.5

### 1.3 Testbed Setup
- [x] `24_setup_testbed.sh setup` runs successfully
- [x] Connectivity verified: `cli → srv` (10.0.0.2 → 10.0.0.1)
- [x] iperf3 server/client test across namespaces works (88 Gbps)
- [x] memcached starts in `srv` namespace

---

## Phase 2: eBPF Tool Development (Week 2)

### 2.1 Core eBPF Scripts
All scripts are located in `ebpf_tools/`.

#### `24_sched_delay.bt` — Runqueue Latency Profiler
- [x] Script created
- [x] Uses `sched_wakeup` + `sched_switch` tracepoints
- [x] In-kernel histogram aggregation (no per-event printf)
- [x] 1-in-1000 sampled CSV output via `@sample_gate`
- [x] Context switch accounting (voluntary/involuntary)
- [x] Overhead claim: target <5%
- [x] Tested standalone: produces valid CSV + histogram output
- [x] Tested under load: histograms show meaningful delay distribution (E1 baseline confirmed)

#### `24_softirq_net.bt` — Network Softirq Duration & Frequency
- [x] Script created
- [x] Tracks NET_RX (vec=3) and NET_TX (vec=2) softirq durations
- [x] Computes softirq CPU% via `@net_rx_total_us[cpu] / wall_clock_us × 100`
- [x] Orphaned event guards: overwrite on re-entry, discard >500ms durations
- [x] `netif_receive_skb` per-CPU packet counter
- [x] No per-event printf — histogram only
- [x] Tested standalone: per-CPU histograms visible with network traffic
- [x] Cross-validated against `/proc/stat` softirq column (proc_pollers confirms)

#### `24_net_drops.bt` — Packet Drops & TCP Retransmissions
- [x] Script created
- [x] `tracepoint:skb:kfree_skb` with `args->location` (not `reason`)
- [x] `kprobe:tcp_retransmit_skb` counter + per-event CSV
- [x] `kprobe:tcp_v4_syn_recv_sock` counter
- [x] Per-CPU drop counts + drop location histogram
- [x] `@syn_recv` printed in END summary
- [x] Tested standalone: drops (with kernel locations) and retransmits captured

#### `24_cpu_migrations.bt` — Task Migration Tracker
- [x] Script created
- [x] `tracepoint:sched:sched_migrate_task` with `orig_cpu`, `dest_cpu`
- [x] Per-process migration count + per-pair migration matrix
- [x] Tested standalone: migration events visible (containerd, bpftrace, etc.)

#### `24_proc_pollers.sh` — /proc Metrics Collector
- [x] Script created
- [x] `collect_cpu()` — per-CPU utilization from `/proc/stat`
- [x] `collect_softnet()` — processed/dropped/time_squeeze from `/proc/net/softnet_stat`
- [x] `collect_tcp()` — header-aware `/proc/net/snmp` parsing (RetransSegs, InSegs, OutSegs)
- [x] `collect_sockstat()` — TCP inuse + mem pages from `/proc/net/sockstat`
- [x] `collect_interrupts()` — veth/virtio/eth interrupt snapshots
- [x] All 5 pollers run in background with 1-second intervals
- [x] Tested standalone: all 5 CSV files generated with valid data (cpu_util, softnet_stat, tcp_stats, sockstat, interrupts)

#### `24_busy_poll_echo_server.c` — Custom Server for SO_BUSY_POLL
- [x] Source created
- [x] `SO_BUSY_POLL` setsockopt on listening socket
- [x] `SO_BUSY_POLL` setsockopt on accepted client sockets
- [x] Multi-threaded (pthread) echo server
- [x] Configurable via CLI args: `<bind_ip> <port> [busy_poll_us]`
- [x] Compiled successfully (`gcc -O2 -o 24_busy_poll_echo_server 24_busy_poll_echo_server.c -lpthread`)
- [x] Tested: echoes data with busy-poll active (E1_test confirmed server starts)

### 2.2 Orchestration Scripts
All scripts in `scripts/`.

#### `24_setup_testbed.sh` — Namespace Setup
- [x] Creates `srv` and `cli` namespaces
- [x] Creates veth pair with correct IPs (10.0.0.1 / 10.0.0.2)
- [x] Sets MTU 1500 and txqueuelen 10000
- [x] Connectivity check built in
- [x] Teardown function for cleanup

#### `24_run_experiment.sh` — Single Experiment Orchestrator
- [x] CLI argument parsing (all 10 parameters)
- [x] `save_sysctl_defaults()` / `restore_sysctl_defaults()` — backup/restore
- [x] `apply_rps_placement()` — default / rps_pinned / rps_spread via `rps_cpus`
- [x] `apply_cfs_tuning()` — default (3ms/4ms/24ms) / lowlatency (1ms/0.5ms/4ms)
- [x] `apply_softirq_mode()` — default (budget=300/8000) / forced_ksoftirqd (50/2000)
- [x] `start_server()` — memcached + iperf3 + conditional echo server (E15/E16)
- [x] `start_ebpf_probes()` — all 4 bpftrace + proc_pollers, stderr→files (not `/dev/null`)
- [x] `run_load()` — iperf3 with warmup, supports TCP/UDP, app pinning
- [x] Metadata JSON saved per run
- [x] Sysctl backup/restore per run
- [x] Cooldown between runs
- [x] End-to-end test: E1_test produced 20 expected output files per run

#### `24_run_all_experiments.sh` — Full Matrix Executor
- [x] All 16 experiments defined (E1–E16)
- [x] Parameters match blueprint experiment matrix (verified column-by-column)
- [x] Phase grouping: Baselines → Placement → Advanced → Mitigations
- [x] Full suite tested: E1–E4 baselines complete (30s × 1 run)

---

## Phase 3: Baseline Experiments (Week 3)

### 3.1 Run Baselines
- [x] E1: None/Low/Default — baseline (3 runs, 60s)
- [x] E2: None/High/Default — softirq pressure (3 runs, 60s)
- [x] E3: Heavy/Low/Default — CPU contention only (3 runs, 60s)
- [x] E4: Heavy/High/Default — worst case (3 runs, 60s)

### 3.2 Baseline Validation
- [x] Data quality check: all output files present per run (11 CSV + 2 JSON per experiment)
- [x] Histogram output is non-empty for `sched_delay` and `softirq_net`
- [x] `/proc` CSV files have expected number of rows (~duration seconds)
- [x] E1 shows low scheduling delay: most events 0–16μs, p99 ~256μs
- [x] E4 shows elevated scheduling delay: dominant at 128–512μs, tail to 1M μs (1 second!)
- [x] Initial CDF plots: runqueue delay overlaid for E1–E4 (`plots/24_cdf_runq_delay_baselines.png`)

---

## Phase 4: Softirq Placement & Pinning (Week 4)

### 4.1 Run Placement Experiments
- [x] E5: Heavy/High/RPS→CPU0 — RPS pinned (3 runs, 60s)
- [x] E6: Heavy/High/RPS→all — RPS spread (3 runs, 60s)
- [x] E7: Heavy/High/Default/Pinned — app pinning only (3 runs, 60s)
- [x] E8: Heavy/High/RPS→CPU0/Pinned — RPS + app pinning (3 runs, 60s)

### 4.2 Validate H1 (Softirq Colocation Hypothesis)
- [x] E4 vs E5 comparison: RPS pinning shifts CPU0 softirq share 3.1%→3.8%, Gini 0.712→0.697
- [x] E4 vs E6 comparison: RPS spread reduces max-CPU fraction 22.8%→16.8%, Gini 0.712→0.662
- [x] E8 analysis: app CPUs 2,3 handle 61.6% of softirqs (colocation, not isolation — softirq follows the socket)
- [x] 5 comparison plots produced (`plots/24_h1_*.png`)

---

## Phase 5: Advanced Experiments (Week 5)

### 5.1 Run Advanced Experiments
- [x] E9: CFS low-latency tuning (3 runs, 60s)
- [x] E10: Forced ksoftirqd via NAPI budget (3 runs, 60s)
- [x] E11: UDP baseline, no CPU stress (3 runs, 60s)
- [x] E12: UDP with CPU stress (3 runs, 60s)
- [x] E13: Moderate CPU contention — inflection point (3 runs, 60s)

### 5.2 Validate H2 & H3
- [x] H2: E10 ksoftirqd p99=4,096μs ≈ E4 p99=4,096μs — no improvement; time_squeeze delta=0 (NAPI budget ineffective on veth)
- [x] H3: E12 UDP p99=4,096μs = E4 TCP p99=4,096μs — equivalent under stress (CPU contention dominates over protocol)
- [x] E13 p99=683μs (below 1ms); E3/E4 p99=4,096μs — threshold crossed between moderate and heavy CPU stress

---

## Phase 6: Mitigation Experiments (Week 6)

### 6.1 Run Mitigation Experiments
- [x] E14: Combined (RPS spread + pinned + lowlatency CFS) (3 runs, 60s)
- [x] E15: RPS spread + SO_BUSY_POLL on custom echo server (3 runs, 60s)
- [x] E16: SO_BUSY_POLL only on custom echo server (3 runs, 60s)

### 6.2 Validate H4 (Combined Mitigations)
- [x] E14 p99=4,096μs ≈ E4 p99=4,096μs — combined mitigations (RPS+pinning+CFS lowlat) did NOT reduce latency
- [x] E15 p99=4,096μs (RPS spread + busy_poll) — busy_poll active, no p99 improvement
- [x] E16 p99=4,096μs (busy_poll only) — busy_poll active, no p99 improvement
- [x] ✅ busy_poll DOES reduce vol_switches by 25%: E16=0.37M vs E4=0.49M (consistent across all runs)
- [x] E14 vol_switches=0.49M (no busy_poll) ≈ E4=0.49M → confirms reduction is from busy_poll specifically
- [x] Bottleneck is CPU contention with stress-ng, not context-switch overhead → p99 unaffected
- [x] No mitigation achieved ≥20% p99 improvement — veth bypasses kernel fast-paths that mitigations target

---

## Phase 7: Analysis & Visualization (Week 7)

### 7.1 Derive Metrics
- [x] Per-experiment: p50, p90, p99, p999 runqueue delay (24_experiment_metrics.csv)
- [x] Per-experiment: softirq CPU fraction per CPU (heatmap generated)
- [x] Per-experiment: context switch rate, packet drop rate, retransmit rate
- [x] Cross-experiment: normalize to E1 baseline, compute degradation factors
- [x] Correlation: softirq Gini coefficient vs scheduling delay (scatter plot)

### 7.2 Produce Plots (34 total in plots/)
- [x] 24_runqueue_delay_cdf_all.png — CDF overlay of all 16 experiments
- [x] 24_percentile_comparison.png — p50/p99/p99.9 bar chart
- [x] 24_softirq_cpu_heatmap.png — CPU × experiment softirq %
- [x] 24_mitigation_p99_comparison.png — before/after mitigation bars
- [x] 24_delay_distribution_boxplot.png — distribution shape per experiment
- [x] 24_voluntary_context_switches.png — context switch counts
- [x] 24_tcp_retransmit_comparison.png — retransmit deltas
- [x] 24_softnet_drops_squeeze.png — packet drops + time_squeeze
- [x] 24_task_cpu_migrations.png — migration counts
- [x] 24_softirq_gini_vs_p99.png — concentration vs latency scatter
- [x] 24_baseline_stress_progression.png — E1-E4 stress CDF
- [x] 24_tcp_udp_delay_comparison.png — H3 TCP vs UDP CDF
- [x] 24_normalized_degradation_factors.png — normalized to E1
- [x] 24_per_cpu_softirq_rps.png — per-CPU with/without RPS
- [x] 24_experiment_summary_table.png — all metrics as table
- [x] + hypothesis validation plots (h1_*, h2_*, h3_*, h4_*)

---

## Phase 8: Report & Packaging (Week 8)

### 8.1 Technical Report
- [x] Background & related work section
- [x] Methodology section (testbed, tools, experiment design)
- [x] Results section (per-hypothesis analysis with plots)
- [x] Discussion section (findings, limitations, future work)
- [x] Conclusions section
- [x] Bibliography / references (18 entries)

### 8.2 Reproducibility Package
- [x] `24_README.md` with exact setup and run instructions
- [x] All scripts executable and tested end-to-end
- [x] Raw data archived (histograms + sampled CSV)
- [x] Python analysis scripts for plot generation
- [x] Single entry point: `24_run_all_experiments.sh`

---

## Blueprint & Script Consistency (Verification)

- [x] `24_sched_delay.bt` matches blueprint §7.3 (histogram + sampling strategy)
- [x] `24_softirq_net.bt` matches blueprint §7.4 (CPU%, guards, no printf)
- [x] `24_net_drops.bt` matches blueprint §7.5 (location, syn_recv, retransmit)
- [x] `24_cpu_migrations.bt` matches blueprint §7.6 (fields, pairs)
- [x] `24_proc_pollers.sh` matches blueprint §7.7 (header-aware, 5 pollers)
- [x] `24_busy_poll_echo_server.c` matches blueprint §9.5 (per-socket SO_BUSY_POLL)
- [x] `24_run_experiment.sh` matches blueprint §5.4 protocol ordering
- [x] `24_run_experiment.sh` stderr→files (histograms preserved, not discarded)
- [x] `24_run_all_experiments.sh` matches blueprint §5.3 matrix (all 16 experiments verified)
- [x] `24_setup_testbed.sh` matches blueprint §4.4 (IPs, namespaces, veth)
- [x] Overhead claim: consistently `<5%` across blueprint and scripts
- [x] No duplicate mitigations (5 total: RPS, app pinning, CFS, NAPI, SO_BUSY_POLL)
- [x] No stale terms (no `irq.affinity`, no unqualified `netfilter`, no `seaborn`)
- [x] HFT relevance stated in problem statement
- [x] Netfilter qualified with "optionally if iptables/nftables rules are active"

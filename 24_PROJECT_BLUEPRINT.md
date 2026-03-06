# Profiling Kernel Scheduling Delays Under Network-Intensive Workloads: An eBPF-Based Analysis and Mitigation Framework

---

## 1. Problem Statement

On commodity Linux systems under sustained network load, the kernel's softirq processing path (NAPI poll, `NET_RX_SOFTIRQ`, TCP/IP stack work) competes directly with user-space application threads for CPU cycles. When softirq execution is colocated with latency-sensitive application threads — and especially when compounded by CPU contention from unrelated processes — scheduling delays escalate non-linearly: runqueue depths grow, CFS `vruntime` accounting skews against newly-woken threads, and wakeup-to-actual-execution latency inflates from microseconds to milliseconds. These tail-latency spikes directly degrade market-data handling and order placement in low-latency trading systems. More broadly, this produces tail latency spikes (p99, and p999 if sufficient request volume) and throughput collapse that are invisible to application-level monitoring and can only be diagnosed through kernel-level tracing. This project uses eBPF to instrument the scheduler, softirq, and network subsystems to quantify these delays, correlate them with packet-processing load, and experimentally validate concrete mitigation strategies including softirq CPU placement control (via RPS and application CPU pinning), CPU isolation, CFS parameter adjustment, and NAPI budget tuning.

> **Primary workload:** memcached + memaslap. This is chosen over nginx/wrk because memcached's request–response model produces clean per-request tail latency (p99 primary; p999 if sufficient request volume) which directly reveals scheduling delay impact. iperf3 is used only as a secondary bandwidth-saturation tool to generate raw softirq pressure without application-level latency measurement.

---

## 2. Objectives

1. **Quantify per-thread scheduling latency distributions** (runqueue delay from `sched_wakeup` → `sched_switch`) under six distinct load profiles combining CPU contention and network intensity.
2. **Measure the causal relationship** between softirq CPU time and application scheduling delay by varying softirq CPU placement across at least 3 topologies (default, RPS-pinned, RPS-spread).
3. **Build a reproducible eBPF instrumentation toolkit** (≥5 bpftrace/BCC scripts) that captures scheduling, softirq, and network metrics with low overhead (target <5% CPU overhead) by using in-kernel histogram aggregation and capped 1-in-1000 sampled CSV output (no per-event printing on hot paths).
4. **Construct a controlled experiment matrix of ≥12 experiments** varying CPU contention, network load, softirq CPU placement (via RPS), application CPU pinning, and CFS tuning parameters.
5. **Produce per-experiment latency histograms, CDF plots, and time-series visualizations** correlating scheduling delay with network throughput degradation.
6. **Identify the threshold of network load** (in pps or Mbps) at which scheduling delay exceeds 1ms p99 for a co-located application under default kernel configuration.
7. **Validate ≥4 mitigation strategies** experimentally, each showing measurable improvement (≥20% p99 latency reduction or ≥10% throughput recovery) versus the unmitigated baseline.
8. **Deliver a reproducible experiment package** (scripts, configs, raw data, analysis notebooks) that can be re-run on any Linux 5.15+ system with ≥4 cores.

---

## 3. Proposed Outcomes

1. **eBPF Instrumentation Toolkit** — A set of 5–7 bpftrace/BCC scripts covering scheduler tracing, softirq profiling, network drop detection, wakeup latency measurement, and per-CPU utilization breakdown.
2. **Experiment Automation Suite** — Bash scripts to orchestrate all 12+ experiments: configure system parameters, launch workloads, trigger eBPF collection, and archive results.
3. **Raw Dataset** — Per-experiment histogram summaries + sampled CSV traces (1-in-1000 events) including timestamps, PIDs, CPU IDs, latency values, and softirq durations. Expected ~50MB–200MB total (histograms are compact; sampled CSV is capped).
4. **Derived Metrics Dataset** — Aggregated per-experiment statistics: p50/p90/p99/p999 scheduling latency, mean softirq time per CPU, context switch rates, packet drop counts, TCP retransmissions.
5. **Visualization Package** — ≥15 publication-quality plots: latency CDFs, heatmaps (CPU × time × softirq%), throughput-vs-latency scatter plots, before/after mitigation comparisons.
6. **Mitigation Validation Report** — Controlled before/after comparison for each of ≥4 mitigations, with statistical significance assessment.
7. **Final Technical Report** — 20–30 page report covering background, methodology, results, analysis, and conclusions.
8. **Reproducibility Package** — `24_README.md` with exact steps, `24_Makefile` for builds, and a single `run_all.sh` entry point.

---

## 4. System Setup

### 4.1 Testbed Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SINGLE MACHINE SETUP                      │
│                                                              │
│  ┌──────────────┐     loopback / veth pair    ┌───────────┐ │
│  │  Load Gen     │◄──────────────────────────►│  Server    │ │
│  │  (netns: cli) │     10 Gbps virtual link   │ (netns:srv)│ │
│  │               │                             │            │ │
│  │  • memaslap   │                             │ • memcached│ │
│  │  • iperf3 -c  │                             │ • iperf3-s │ │
│  └──────────────┘                             └───────────┘ │
│                                                              │
│  ┌──────────────┐          ┌──────────────────────────────┐  │
│  │ CPU Stressor │          │  eBPF Probes (system-wide)   │  │
│  │  stress-ng   │          │  bpftrace / BCC tools        │  │
│  │  (configurable│         │  perf stat (validation)      │  │
│  │   core count) │         └──────────────────────────────┘  │
│  └──────────────┘                                            │
└─────────────────────────────────────────────────────────────┘
```

**Why single machine with network namespaces:** Eliminates NIC hardware variability, ensures reproducibility, and still exercises the full kernel network stack (socket layer → TCP/IP → qdisc → virtual NIC → peer namespace; optionally netfilter if iptables/nftables rules are active). The `veth` pair generates real softirq load.

> **Important note on veth vs. physical NIC:** A veth pair does **not** generate hardware IRQs with stable IRQ numbers the way a physical NIC does. Softirq processing for veth is triggered by `netif_rx()` / NAPI within the sending namespace's context or via `NET_RX_SOFTIRQ` on the receiving side. This means classical `/proc/irq/<N>/smp_affinity` tuning does not apply to veth. Our experiments control softirq CPU placement using **RPS** (`rps_cpus`), **application CPU pinning** (`taskset`), and **NAPI budget tuning** instead. See Section 9.1 for the honest treatment.

### 4.2 Software Requirements

| Component | Version / Requirement | Notes |
|---|---|---|
| **OS** | Ubuntu 22.04 LTS or 24.04 LTS | Widely available, good eBPF support |
| **Kernel** | ≥ 5.15 (recommended 6.1+) | BTF support required; `CONFIG_DEBUG_INFO_BTF=y` |
| **bpftrace** | ≥ 0.17.0 | For `kfunc` support and reliable BTF parsing |
| **BCC tools** | ≥ 0.25.0 | `runqlat`, `softirqs`, `cpudist` |
| **iproute2** | ≥ 5.15 | For `ip netns`, veth management |
| **memcached** | ≥ 1.6 | **Primary workload**: latency-sensitive key-value server |
| **libmemcached-tools** | ≥ 1.0 | Provides `memaslap` load generator with p99 latency tracking |
| **iperf3** | ≥ 3.9 | Secondary: bandwidth saturation tool for raw softirq pressure |
| **stress-ng** | ≥ 0.13 | CPU contention generator |
| **perf** | Matching kernel version | Cross-validation of eBPF data |
| **python3** | ≥ 3.9 | With `matplotlib`, `pandas`, `numpy` (no seaborn — deterministic output) |

### 4.3 Hardware Assumptions

| Parameter | Minimum | Recommended |
|---|---|---|
| **CPU Cores** | 4 (physical or vCPU) | 8+ for meaningful isolation experiments |
| **RAM** | 4 GB | 8 GB (memcached workload + trace buffers) |
| **Storage** | 20 GB free | 50 GB (for raw traces) |
| **NIC** | Any (using veth) | Physical NIC for optional stretch-goal experiments |

### 4.4 Namespace & Network Setup Script

```bash
#!/bin/bash
# 24_setup_testbed.sh — Create isolated network namespaces for experiments

set -euo pipefail

# Create namespaces
ip netns add srv
ip netns add cli

# Create veth pair
ip link add veth-srv type veth peer name veth-cli

# Move interfaces into namespaces
ip link set veth-srv netns srv
ip link set veth-cli netns cli

# Configure IP addresses
ip netns exec srv ip addr add 10.0.0.1/24 dev veth-srv
ip netns exec cli ip addr add 10.0.0.2/24 dev veth-cli

# Bring up interfaces
ip netns exec srv ip link set veth-srv up
ip netns exec srv ip link set lo up
ip netns exec cli ip link set veth-cli up
ip netns exec cli ip link set lo up

# Set MTU (standard)
ip netns exec srv ip link set veth-srv mtu 1500
ip netns exec cli ip link set veth-cli mtu 1500

# Optional: increase txqueuelen for high-throughput tests
ip netns exec srv ip link set veth-srv txqueuelen 10000
ip netns exec cli ip link set veth-cli txqueuelen 10000

echo "[OK] Testbed ready: srv=10.0.0.1, cli=10.0.0.2"
```

---

## 5. Experimental Design

### 5.1 Independent Variables

| Variable | Levels | How to Set |
|---|---|---|
| **CPU contention** | None / Moderate (2 cores stressed) / Heavy (all cores stressed) | `stress-ng --cpu N --cpu-method matrixprod --timeout 120s` |
| **Network load** | Low (~100 Mbps) / High (~saturated, max pps) | `iperf3 -c 10.0.0.1 -b 100M` vs `iperf3 -c 10.0.0.1 -b 0` (unlimited) |
| **Softirq CPU placement** | Default / Pinned via RPS to CPU 0 / Spread via RPS across all CPUs | `echo <mask> > /sys/class/net/veth-srv/queues/rx-0/rps_cpus` (veth has no hardware IRQ to pin; we control softirq placement via RPS). We validate RPS is active by observing per-CPU packet receive distribution via `24_softirq_net.bt` + `netif_receive_skb` counters. |
| **App CPU pinning** | None / Pinned to isolated CPUs | `taskset -c 2,3 memcached` or `numactl --physcpubind=2,3` |
| **CFS tuning** | Default / Low-latency (`sched_min_granularity_ns=1ms`, `sched_wakeup_granularity_ns=0.5ms`) | Write to `/proc/sys/kernel/sched_*` |
| **Softirq mode** | Default / Forced ksoftirqd (reduce `netdev_budget` to 50) | `/proc/sys/net/core/netdev_budget` and `netdev_budget_usecs` |
| **Protocol** | TCP / UDP | `iperf3 -u` flag |

### 5.2 Dependent Metrics (What We Measure)

| Category | Metric | Unit | Collection Method |
|---|---|---|---|
| Scheduling | Runqueue delay (wakeup→run) | μs | eBPF: `sched_wakeup` → `sched_switch` delta |
| Scheduling | Context switch rate | switches/sec | `perf stat -e context-switches` + eBPF |
| Scheduling | CPU migrations | count | `perf stat -e cpu-migrations` + eBPF `sched_migrate_task` |
| Scheduling | Per-CPU utilization split | % | `/proc/stat` parsing (user/sys/softirq/irq/idle) |
| Scheduling | CFS vruntime spread | ns | eBPF kprobe on `pick_next_task_fair` (stretch) |
| Network | Packet drops | count | `/proc/net/softnet_stat` column 2 + eBPF `kfree_skb` |
| Network | TCP retransmissions | count | eBPF `kprobe:tcp_retransmit_skb` + `ss -ti` |
| Network | Softnet backlog | count | `/proc/net/softnet_stat` column 1 |
| Network | Softirq time per CPU | μs | eBPF: `softirq_entry` → `softirq_exit` delta |
| Network | **Softirq CPU fraction** | % | `sum(softirq_duration_us per CPU) / wall_clock_us × 100` — computed from `24_softirq_net.bt` cumulative sums. Guards: overwrite start on re-entry (handles missed exits), discard durations >500ms (orphaned events). Cross-validated against `/proc/stat` softirq column deltas. |
| Network | Socket buffer pressure | 0-1 | `/proc/net/sockstat` (mem usage vs limits) |
| Application | Throughput | Mbps or req/s | `iperf3` JSON output / `memaslap` stats |
| Application | Latency p50/p90/p99 (p999 if sufficient samples) | ms | `memaslap --stat_freq=1s` / custom eBPF histogram |

### 5.3 Experiment Matrix (16 Experiments)

| Exp# | CPU Contention | Net Load | Softirq Placement (RPS) | App Pinning | CFS Tuning | Softirq Mode | Protocol | Hypothesis |
|---|---|---|---|---|---|---|---|---|
| **E1** | None | Low | Default | None | Default | Default | TCP | **Baseline**: low scheduling delay, high throughput |
| **E2** | None | High | Default | None | Default | Default | TCP | Softirq rises, moderate scheduling delay increase |
| **E3** | Heavy | Low | Default | None | Default | Default | TCP | CPU contention causes scheduling delay even without net load |
| **E4** | Heavy | High | Default | None | Default | Default | TCP | **Worst case**: scheduling delay spikes, throughput collapses |
| **E5** | Heavy | High | RPS→CPU 0 | None | Default | Default | TCP | RPS pinning concentrates softirq on CPU 0, frees other cores |
| **E6** | Heavy | High | RPS→all CPUs | None | Default | Default | TCP | RPS spread distributes softirq load more evenly |
| **E7** | Heavy | High | Default | Pinned (2,3) | Default | Default | TCP | App pinning isolates from softirq cores if lucky |
| **E8** | Heavy | High | RPS→CPU 0 | Pinned (2,3) | Default | Default | TCP | **Key mitigation**: RPS + app pinning should yield best isolation |
| **E9** | Heavy | High | Default | None | Low-latency | Default | TCP | CFS tuning reduces granularity → faster preemption |
| **E10** | Heavy | High | Default | None | Default | Forced ksoftirqd | TCP | More ksoftirqd → more schedulable softirq → fairer sharing |
| **E11** | None | High | Default | None | Default | Default | UDP | UDP baseline: no retransmit overhead, raw pps pressure |
| **E12** | Heavy | High | Default | None | Default | Default | UDP | UDP under contention: compare with TCP E4 |
| **E13** | Moderate | High | Default | None | Default | Default | TCP | Moderate contention: find the inflection point |
| **E14** | Heavy | High | RPS→all CPUs | Pinned (2,3) | Low-latency | Default | TCP | **Combined mitigations**: best-effort optimal config |
| **E15** | Heavy | High | RPS→all CPUs | None | Default | Default | TCP | **Stacked mitigation**: RPS spread + `SO_BUSY_POLL` on a custom TCP echo server (tests combined steering + busy poll) |
| **E16** | Heavy | High | Default | None | Default | Default | TCP | `SO_BUSY_POLL` on a custom TCP echo server (busy polling without RPS) |

### 5.4 Per-Experiment Protocol

```
For each experiment E_i:
  1. Reset system: stop irqbalance, reset /proc/sys/kernel/sched_*, flush conntrack
  2. Apply configuration (RPS softirq placement, CPU pinning, CFS params, etc.)
  3. Start server workload (memcached for latency experiments, iperf3 -s for bandwidth-only)
  4. Start eBPF probes (all scripts in parallel, writing to /data/E_i/)
  5. Start /proc/stat and /proc/net/softnet_stat pollers (1s interval)
  6. Start CPU stressors (if applicable)
  7. Stabilize: 2s settle time
  8. Warm up: 10s of load without measurement
  9. Start measurement window: 60s of sustained load
 10. Stop all processes, archive data
 11. Cooldown: 10s idle
 12. Repeat 3 times for statistical validity
```

---

## 6. Metrics Measurement — Exact Methods

### 6.A Scheduling Metrics

#### 6.A.1 Runqueue Delay / Scheduling Latency Per Thread

**Mechanism:** Attach to `tracepoint:sched:sched_wakeup` to record {PID, timestamp, target_cpu} into a BPF hash map. On `tracepoint:sched:sched_switch`, if `next_pid` matches a stored entry, compute `delta = now - wakeup_ts`. This delta is the runqueue delay.

```c
// Pseudocode for BPF program
struct key_t { u32 pid; };
BPF_HASH(wakeup_ts, struct key_t, u64);

// On sched_wakeup:
key.pid = args->pid;
wakeup_ts.update(&key, &ts);

// On sched_switch (next_pid):
u64 *tsp = wakeup_ts.lookup(&key);
if (tsp) {
    u64 delta = bpf_ktime_get_ns() - *tsp;
    // Store in histogram or ring buffer
}
```

**Attribution:** The PID field in both tracepoints identifies the process. `args->target_cpu` in `sched_wakeup` gives the target CPU; `sched_switch` fires on the actual CPU.

**Output:** Primary: in-kernel histogram buckets (1μs, 10μs, 100μs, 1ms, 10ms, 100ms) — zero userspace overhead. Secondary: 1-in-1000 sampled CSV events (`timestamp_ns, pid, comm, cpu, delay_us`) for spot-checking and time-series analysis.

#### 6.A.2 Wakeup-to-Run Latency

Same mechanism as above. The delta between `sched_wakeup` and the corresponding `sched_switch` where `next_pid == woken_pid` is exactly the wakeup-to-run latency.

**Tool:** BCC's `runqlat` does exactly this. Run as: `runqlat -p <PID> --json 1 60` to get per-second histograms for 60 seconds.

#### 6.A.3 Context Switch Rate

**Method 1:** `perf stat -e context-switches -a -I 1000 -- sleep 60` — system-wide, 1s intervals.

**Method 2:** eBPF on `sched_switch` with a per-CPU counter incremented on each event. Print every second.

**Method 3:** Parse `/proc/stat` field `ctxt` at 1-second intervals, compute delta.

#### 6.A.4 CPU Migrations

**Tracepoint:** `tracepoint:sched:sched_migrate_task` — fires when a task moves between CPUs.

**Fields:** `pid`, `comm`, `orig_cpu`, `dest_cpu`.

**Tool:** `perf stat -e cpu-migrations -a` for aggregate; eBPF for per-process breakdown.

#### 6.A.5 Per-CPU Utilization Split

**Method:** Parse `/proc/stat` every 1 second. Each CPU line has: `user, nice, system, idle, iowait, irq, softirq, steal`.

```bash
# proc_stat_poller.sh
while true; do
    echo "$(date +%s.%N),$(grep '^cpu' /proc/stat | tr '\n' '|')" >> cpu_util.csv
    sleep 1
done
```

Compute percentages as deltas between consecutive readings.

### 6.B Network Metrics

#### 6.B.1 Packet Drops (Kernel-Level)

**Method 1:** `/proc/net/softnet_stat` — Column 2 (0-indexed col 1) is `dropped`. Poll every 1 second, compute delta.

```bash
# Format: processed, dropped, time_squeeze, ..., cpu_collision, received_rps, flow_limit_count
cat /proc/net/softnet_stat
```

**Method 2:** eBPF `tracepoint:skb:kfree_skb` to catch every dropped packet with drop location. On kernel ≥5.17, use `kprobe:kfree_skb_reason` for a human-readable drop reason code.

#### 6.B.2 TCP Retransmissions

**Method 1:** `kprobe:tcp_retransmit_skb` — fires on every retransmit. Record: `saddr`, `daddr`, `sport`, `dport`, `state`.

**Method 2:** `ss -ti` — parse `retrans:` field periodically.

**Method 3:** `/proc/net/snmp` — `Tcp: RetransSegs` counter, poll and diff. **Note:** `/proc/net/snmp` uses paired lines (header then values) with the same prefix `Tcp:`. The parser must read the header line to identify field positions, then extract values by index. See `24_proc_pollers.sh:collect_tcp()` for the correct header-aware implementation.

#### 6.B.3 Softnet Backlog

**Method:** `/proc/net/softnet_stat` column 1 (processed count) and column 3 (time_squeeze — number of times softirq hit its budget limit and deferred). Time_squeeze directly indicates backlog pressure.

#### 6.B.4 Socket Buffer Pressure

**Method:** `/proc/net/sockstat` — check `TCP: inuse`, `mem` fields. Compare `mem` to `sysctl net.ipv4.tcp_mem` limits.

```bash
cat /proc/net/sockstat
# TCP: inuse 45 orphan 0 tw 12 alloc 60 mem 150
# mem is in pages (4KB). Compare to tcp_mem third value (max).
```

### 6.C Application-Level Metrics

#### 6.C.1 Throughput

- **memaslap:** `memaslap -s 10.0.0.1:11211 -T 4 -c 100 -t 60s --stat_freq=1s` → parse `ops/sec` from output.
- **iperf3:** `iperf3 -c 10.0.0.1 -t 60 -J` → parse `bits_per_second` from JSON output (bandwidth-only experiments).

#### 6.C.2 Tail Latency

- **memaslap:** Reports percentile distribution (avg, min, max, p99) natively per stat interval. This is the primary source of application-level tail latency.
- **Custom:** Use eBPF to timestamp at `sock_recvmsg` entry/exit for server-side processing latency.

---

## 7. eBPF Instrumentation Plan

### 7.1 Tracepoints and Kprobes to Hook

| Hook Point | Type | Fields to Record | Purpose |
|---|---|---|---|
| `sched:sched_wakeup` | tracepoint | `pid, comm, target_cpu, prio, ts` | Start of runqueue wait |
| `sched:sched_wakeup_new` | tracepoint | `pid, comm, target_cpu, prio, ts` | New thread creation wakeup |
| `sched:sched_switch` | tracepoint | `prev_pid, prev_comm, prev_state, next_pid, next_comm, cpu, ts` | End of runqueue wait / context switch |
| `sched:sched_migrate_task` | tracepoint | `pid, comm, orig_cpu, dest_cpu` | CPU migration tracking |
| `irq:softirq_entry` | tracepoint | `vec (NET_RX=3, NET_TX=2), ts, cpu` | Softirq duration start |
| `irq:softirq_exit` | tracepoint | `vec, ts, cpu` | Softirq duration end |
| `irq:irq_handler_entry` | tracepoint | `irq, name, cpu, ts` | Hard IRQ timing |
| `irq:irq_handler_exit` | tracepoint | `irq, ret, ts` | Hard IRQ duration |
| `net:netif_receive_skb` | tracepoint | `name, len, cpu` | Packet arrival rate per CPU |
| `skb:kfree_skb` | tracepoint | `skbaddr, protocol, location` | Packet drops (location only; for reason field use `kprobe:kfree_skb_reason` on kernel ≥5.17) |
| `tcp_retransmit_skb` | kprobe | `saddr, daddr, sport, dport, state` | TCP retransmits |
| `tcp_rcv_established` | kprobe | (optional) for per-connection latency | TCP processing time |

### 7.2 Overhead Reduction Strategy

1. **Per-CPU hash maps** instead of global maps → no lock contention.
2. **In-kernel histograms** (BPF_HISTOGRAM) → aggregate in kernel, only read summary from userspace. **All hot-path scripts (`24_sched_delay.bt`, `24_softirq_net.bt`) use histogram-only output with NO per-event printf on the hot path.** This is the single most important overhead control.
3. **Capped sampled CSV output:** For spot-checking and time-series analysis, `24_sched_delay.bt` prints 1-in-1000 events. At 1M events/sec this means ~1000 printf/sec — trivial overhead vs. 1M/sec.
4. **Sampling for packet-rate events:** `netif_receive_skb` uses counter-only aggregation (`count()`, `hist()`), not per-event output.
5. **Process filtering:** For per-app metrics, filter by PID/TGID in BPF program (`if (pid != target_pid) return 0;`).
6. **Batch reads:** Read maps/histograms every 1–5 seconds, not per-event from userspace.

### 7.3 Script 1: `24_sched_delay.bt` — Runqueue Latency Profiler

**Strategy:** Histogram-only aggregation in-kernel. No per-event `printf` on the hot path. A 1-in-1000 sampled CSV line is emitted for spot-checking and time-series reconstruction. This keeps overhead low (target <5%) even at millions of `sched_switch`/sec.

See [`ebpf_tools/24_sched_delay.bt`](file:///home/iiitd/Desktop/MT25037/ebpf_tools/24_sched_delay.bt) for the full implementation.

### 7.4 Script 2: `24_softirq_net.bt` — Network Softirq Duration & Frequency

**Strategy:** Histogram-only aggregation, no per-event `printf`. Computes **softirq CPU%** as `sum(softirq_duration_us) / wall_clock_us × 100` using cumulative `@net_rx_total_us[cpu]` sums.

**Nested/orphaned event guards:** On `softirq_entry`, the start timestamp is overwritten unconditionally (handles missed exit). On `softirq_exit`, durations >500ms are discarded as orphaned events. This ensures we never compute bogus multi-second durations from mismatched entry/exit pairs.

See [`ebpf_tools/24_softirq_net.bt`](file:///home/iiitd/Desktop/MT25037/ebpf_tools/24_softirq_net.bt) for the full implementation.

### 7.5 Script 3: `24_net_drops.bt` — Packet Drops & TCP Retransmissions

```c
#!/usr/bin/env bpftrace
/*
 * 24_net_drops.bt — Track kernel packet drops and TCP retransmissions
 * Output: CSV of drop/retransmit events
 * Usage: bpftrace 24_net_drops.bt > net_drops.csv
 */

BEGIN
{
    printf("timestamp_ns,event_type,cpu,pid,comm,detail\n");
}

// Packet drops (tracepoint provides location only; use kprobe:kfree_skb_reason on kernel ≥5.17 for reason)
tracepoint:skb:kfree_skb
{
    @drops[cpu] = count();
    @drop_locations[args->location] = count();

    printf("%lu,pkt_drop,%d,%d,%s,loc=0x%lx\n",
        nsecs, cpu, pid, comm, args->location);
}

// TCP retransmissions
kprobe:tcp_retransmit_skb
{
    @retrans = count();

    printf("%lu,tcp_retransmit,%d,%d,%s,retransmit\n",
        nsecs, cpu, pid, comm);
}

// TCP receive queue overflow
kprobe:tcp_v4_syn_recv_sock
{
    @syn_recv = count();
}

interval:s:5
{
    time();
    printf("\n--- Drops per CPU ---\n");
    print(@drops);
    printf("--- Total retransmits ---\n");
    print(@retrans);
}

END
{
    printf("\n=== FINAL SUMMARY ===\n");
    printf("Drop locations:\n");
    print(@drop_locations);
    printf("Drops per CPU:\n");
    print(@drops);
    printf("Total retransmits: ");
    print(@retrans);
}
```

### 7.6 Script 4: `24_cpu_migrations.bt` — Task Migration Tracker

```c
#!/usr/bin/env bpftrace
/*
 * 24_cpu_migrations.bt — Track per-process CPU migrations
 * Usage: bpftrace 24_cpu_migrations.bt > migrations.csv
 */

BEGIN
{
    printf("timestamp_ns,pid,comm,orig_cpu,dest_cpu\n");
}

tracepoint:sched:sched_migrate_task
{
    printf("%lu,%d,%s,%d,%d\n",
        nsecs, args->pid, args->comm, args->orig_cpu, args->dest_cpu);

    @migrations[args->comm] = count();
    @migration_pairs[args->orig_cpu, args->dest_cpu] = count();
}

interval:s:10
{
    time();
    print(@migrations);
}

END
{
    printf("\n=== Migration Summary ===\n");
    print(@migrations);
    print(@migration_pairs);
}
```

### 7.7 Script 5: `24_proc_pollers.sh` — /proc Metrics Collector

Collects CPU utilization, softnet stats, TCP SNMP counters, socket stats, and interrupt snapshots at 1-second intervals.

**Note on `/proc/net/snmp` parsing:** The TCP stats collector uses header-aware field matching (reads field names from the header line, maps to column indices) instead of hardcoded column numbers. This is stable across kernel versions.

See [`ebpf_tools/24_proc_pollers.sh`](file:///home/iiitd/Desktop/MT25037/ebpf_tools/24_proc_pollers.sh) for the full implementation.

### 7.8 Output Formats

> **Note (bpftrace v0.21+):** Unlike older versions, bpftrace v0.21+ sends **all** output (`printf()` and `print()`) to **stdout**. The `-q` flag suppresses "Attaching N probes..." messages. `24_run_experiment.sh` captures everything in a single `_raw.txt` file per tool, then uses `split_bpftrace_output()` to separate CSV lines from histogram/summary data via grep.

**Primary output: in-kernel histograms** (printed by bpftrace at each interval and at END). Post-process histogram text with a Python parser to extract percentiles.

**Secondary output: sampled CSV** (interleaved in the same stream). Only `24_sched_delay.bt` emits sampled CSV (1-in-1000). Other scripts use histograms and counters only.

| Script | CSV data | Histogram / summary data |
|---|---|---|
| `24_sched_delay.bt` | `timestamp_ns, pid, comm, cpu, delay_us, event` (1-in-1000 sampled) | Runqueue delay histograms, per-process histograms, context switch counts |
| `24_softirq_net.bt` | CSV header for interval metadata (interval_s, cpu, totals, wall, softirq_cpu_pct) | Duration histograms per CPU, cumulative softirq time sums, packet counts, wall clock |
| `24_net_drops.bt` | `timestamp_ns, event_type, cpu, pid, comm, detail` (every drop/retransmit — low rate) | Aggregate counters |
| `24_cpu_migrations.bt` | `timestamp_ns, pid, comm, orig_cpu, dest_cpu` (every migration — moderate rate) | Per-process and per-pair counts |
| `24_proc_pollers.sh` | Multiple files: `cpu_util.csv`, `softnet_stat.csv`, `tcp_stats.csv`, `sockstat.csv`, `interrupts.csv` | — |

---

## 8. Analysis Plan

### 8.1 Step-by-Step Workflow

```
1. RUN EXPERIMENT
   ├── Apply system configuration for experiment E_i
   ├── Launch server + eBPF probes + /proc pollers
   ├── Launch load generator + optional CPU stressor
   └── Collect for 60s, repeat 3 times

2. COLLECT & ORGANIZE
   ├── Archive: /data/E_i/run_{1,2,3}/{sched_delay,softirq_net,net_drops,...}.csv
   ├── Copy /proc poller outputs
   └── Save iperf3/wrk JSON outputs

3. COMPUTE DERIVED METRICS
   ├── Per-experiment:
   │   ├── Runqueue delay: p50, p90, p99, p999, mean, max
   │   ├── Softirq time fraction per CPU: sum(softirq_duration) / wall_clock
   │   ├── Context switch rate: events/sec
   │   ├── Packet drop rate: drops/sec
   │   └── TCP retransmit rate: retransmits/sec
   └── Cross-experiment:
       ├── Normalize to baseline (E1)
       └── Compute relative degradation factors

4. CORRELATE
   ├── Scheduling delay vs. throughput: scatter plot with regression
   ├── Softirq CPU% vs. scheduling delay: per-CPU correlation
   ├── Time_squeeze count vs. p99 latency: correlation coefficient
   └── CPU migration count vs. latency variance: stability analysis

5. PRODUCE PLOTS
   ├── CDF of runqueue delay (overlay all experiments)
   ├── Heatmap: CPU × experiment × softirq%
   ├── Time series: throughput + p99 latency + softirq% (3-axis)
   ├── Bar chart: mitigation comparison (before/after)
   ├── Box plot: runqueue delay distribution per experiment
   └── Scatter: softirq CPU time vs. wakeup latency
```

### 8.2 Hypotheses

#### Hypothesis 1: Softirq Colocation Causes Scheduling Delay

**Statement:** When NET_RX softirq processing runs on the same CPU as the application's hot threads, runqueue delay for those threads increases by ≥5× compared to when they run on separate CPUs.

**Validation:**
- Compare E4 (default, colocated) vs. E8 (RPS pinned to CPU 0, app pinned to CPU 2,3).
- Metric: p99 runqueue delay from `24_sched_delay.bt`, filtered by app PID.
- Expected: E4 p99 > 1ms, E8 p99 < 200μs.
- **Falsification:** If E8 shows no significant improvement, colocation is not the primary driver — check if CFS is the bottleneck instead.

#### Hypothesis 2: CPU Contention Amplifies Network-Induced Scheduling Delays Non-Linearly

**Statement:** Under high network load, adding CPU contention increases scheduling delay super-linearly (not just additively).

**Validation:**
- Compare E2 (high net, no CPU stress), E3 (low net, high CPU stress), and E4 (high net + high CPU stress).
- If `delay(E4) > delay(E2) + delay(E3)`, the interaction is super-linear.
- Metric: mean and p99 runqueue delay.
- **Falsification:** If `delay(E4) ≈ delay(E2) + delay(E3)`, effects are independent → simpler mitigation possible.

#### Hypothesis 3: CFS Low-Latency Tuning Improves Preemption Under Load

**Statement:** Reducing `sched_min_granularity_ns` and `sched_wakeup_granularity_ns` reduces p99 runqueue delay by ≥30% under heavy load, at the cost of ≤5% throughput reduction due to more context switches.

**Validation:**
- Compare E4 (default CFS) vs. E9 (low-latency CFS).
- Metrics: p99 runqueue delay (should decrease) + context switch rate (should increase) + throughput (should slightly decrease).
- **Falsification:** If p99 doesn't improve, CFS granularity isn't the bottleneck — the delay is in the runqueue depth itself, not preemption latency.

#### Hypothesis 4: RPS Distributes Softirq Load and Reduces Per-CPU Scheduling Impact

**Statement:** Enabling RPS (Receive Packet Steering) across all CPUs distributes NET_RX softirq processing, reducing the maximum per-CPU softirq fraction by ≥50% and improving application scheduling on the previously-overloaded CPU.

**Validation:**
- Compare E4 (no RPS) vs. E6 (RPS spread across all CPUs) vs. E15 (RPS spread + SO_BUSY_POLL).
- Metrics: per-CPU softirq time fraction from `24_softirq_net.bt` + runqueue delay from `24_sched_delay.bt`.
- **Falsification:** If RPS introduces inter-CPU overhead (IPI storms) that negates the benefit, total latency may worsen. If busy_poll on top of RPS adds CPU waste without latency gain, E15 ≈ E6.

---

## 9. Mitigations

### 9.1 Mitigation 1: Softirq CPU Placement via RPS

**What:** Use RPS (Receive Packet Steering) to control which CPU processes incoming packets' `NET_RX_SOFTIRQ`. This is the correct mechanism for veth interfaces — veth pairs do **not** have hardware IRQ lines that can be pinned via `/proc/irq/<N>/smp_affinity`. Unlike a physical NIC, veth packet delivery triggers softirq directly via `netif_rx()` / NAPI polling, so the only way to steer softirq CPU placement is through RPS.

**How to apply:**
```bash
# Pin softirq to CPU 0 only (bitmask 1)
echo 1 > /sys/class/net/veth-srv/queues/rx-0/rps_cpus

# Or spread across all CPUs (4-CPU system → mask f)
echo f > /sys/class/net/veth-srv/queues/rx-0/rps_cpus

# Set RPS flow table size for connection affinity
echo 32768 > /sys/class/net/veth-srv/queues/rx-0/rps_flow_cnt
echo 32768 > /proc/sys/net/core/rps_sock_flow_entries

# Disable irqbalance (irrelevant for veth, but ensures no interference)
systemctl stop irqbalance
```

**Expected impact:** RPS→CPU 0 concentrates softirq on one core, freeing others for application threads (40–60% p99 improvement on non-softirq CPUs). RPS→all spreads load evenly but adds IPI overhead.

**Risk/tradeoff:** RPS→CPU 0 makes that CPU a bottleneck under saturation. RPS→all adds inter-processor interrupts (IPIs). Best combined with application CPU pinning (Mitigation 2).

**Tested in:** E5, E6, E8, E15.

---

### 9.2 Mitigation 2: Application CPU Pinning with `taskset` / `cpuset`

**What:** Pin application threads to specific CPUs that don't handle softirq (as controlled by RPS in M1).

**How to apply:**
```bash
# Pin memcached to CPUs 2,3
taskset -c 2,3 memcached -m 256 -t 2 -l 10.0.0.1

# Or use cgroups v2 cpuset
mkdir -p /sys/fs/cgroup/app
echo "2-3" > /sys/fs/cgroup/app/cpuset.cpus
echo "0" > /sys/fs/cgroup/app/cpuset.mems
echo $MEMCACHED_PID > /sys/fs/cgroup/app/cgroup.procs
```

**Expected impact:** Combined with RPS pinning (M1), creates clean separation — 20–50% p99 latency reduction.

**Risk/tradeoff:** If pinned CPUs are too few, app threads contend with each other. Must size correctly relative to workload.

**Tested in:** E7, E8.

---

### 9.3 Mitigation 3: CFS Scheduler Tuning

**What:** Reduce CFS scheduling granularity to allow more frequent preemption, so woken threads run sooner.

**How to apply:**
```bash
# Default values (for reference):
# sched_min_granularity_ns = 3000000 (3ms)
# sched_wakeup_granularity_ns = 4000000 (4ms)

# Low-latency tuning:
echo 1000000 > /proc/sys/kernel/sched_min_granularity_ns      # 1ms
echo 500000  > /proc/sys/kernel/sched_wakeup_granularity_ns    # 0.5ms
echo 4000000 > /proc/sys/kernel/sched_latency_ns               # 4ms (reduce from 24ms)
```

**Expected impact:** 20–40% reduction in p99 runqueue delay. More frequent context switches (10–30% increase) — slight throughput cost.

**Risk/tradeoff:** Higher context switch rate increases cache thrashing and overhead. On highly threaded workloads, throughput may degrade more than latency improves. Reversible with sysctl restore.

**Tested in:** E9, E14.

---

### 9.4 Mitigation 4: Reducing NAPI Budget (Force `ksoftirqd`)

**What:** Reduce `netdev_budget` so softirq processing is capped more aggressively, causing work to be deferred to `ksoftirqd` threads (which are schedulable by CFS, unlike inline softirq).

**How to apply:**
```bash
# Default: netdev_budget=300, netdev_budget_usecs=8000
echo 50   > /proc/sys/net/core/netdev_budget
echo 2000 > /proc/sys/net/core/netdev_budget_usecs
```

**Expected impact:** Softirq processing becomes more interruptible. Application threads preempt ksoftirqd more fairly. p99 scheduling delay should decrease. Time_squeeze counter will increase (expected).

**Risk/tradeoff:** Total network throughput may drop 5–15% as processing is deferred. Packet drops may increase under saturation if ksoftirqd can't keep up.

**Tested in:** E10.

---

### 9.5 Mitigation 5: `SO_BUSY_POLL` (Busy Polling)

**What:** Application socket polls the NIC directly, bypassing softirq path entirely. Trades CPU cycles for latency.

> **Important:** `SO_BUSY_POLL` is a per-socket `setsockopt` — it cannot be transparently applied to memcached without patching its source. For E15 and E16, we use a **custom minimal TCP echo server** (≈50 lines of C) that sets `SO_BUSY_POLL` on its listening socket. This isolates the busy-poll effect cleanly. memcached remains the primary workload for all other experiments.

**How to apply:**
```bash
# System-wide default busy poll time (μs) — enables kernel-side support
echo 50 > /proc/sys/net/core/busy_poll
echo 50 > /proc/sys/net/core/busy_read

# Per-socket (in the custom echo server code):
# int val = 50; setsockopt(fd, SOL_SOCKET, SO_BUSY_POLL, &val, sizeof(val));
```

**Expected impact:** Eliminates softirq-to-scheduling delay for polled sockets. Latency reduction of 50–80% for low-connection-count workloads. CPU usage increases (spinning).

**Risk/tradeoff:** Wastes CPU cycles when idle. Bad for multi-tenant or many-connection workloads. Best for latency-critical servers with dedicated CPUs. Results from the custom echo server may not directly generalize to memcached’s multi-threaded architecture.

**Tested in:** E15, E16.

---

## 10. Timeline

### Week-by-Week Plan (8 Weeks)

| Week | Phase | Deliverables |
|---|---|---|
| **Week 1** | **Environment Setup** | Install Ubuntu VM/server, kernel ≥5.15 with BTF. Install bpftrace, BCC, iperf3, memcached, libmemcached-tools, stress-ng. Create network namespace testbed. Verify all tools run. Validate eBPF with a trivial probe. |
| **Week 2** | **eBPF Tool Development** | Implement all 5 bpftrace scripts. Test each independently. Validate output formats. Write `24_proc_pollers.sh`. Create experiment orchestration scripts (`24_run_experiment.sh`). **→ MVP checkpoint** |
| **Week 3** | **Baseline Experiments** | Run E1–E4 (baseline matrix: ±CPU contention × ±network load). Collect 3 runs each. Validate data quality. Produce initial plots: runqueue delay CDFs, softirq time fractions. |
| **Week 4** | **Softirq Placement & Pinning Experiments** | Run E5–E8 (RPS placement + app pinning variations). Compare against baselines. Produce before/after comparison plots. Validate H1 (softirq colocation). |
| **Week 5** | **Advanced Experiments** | Run E9–E12 (CFS tuning, ksoftirqd, UDP). Validate H2, H3. Compute interaction effects. Run E13 (moderate contention inflection point). |
| **Week 6** | **Mitigation Experiments** | Run E14–E16 (combined mitigations, RPS, busy poll). Full before/after analysis. Validate H4. Identify best overall configuration. |
| **Week 7** | **Analysis & Visualization** | Compute all derived metrics. Produce final plot set (≥15 plots). Statistical analysis: confidence intervals, correlation coefficients. Draft conclusions. |
| **Week 8** | **Report & Packaging** | Write final technical report. Package reproducibility kit. Review and polish. Prepare presentation if needed. |

### MVP (Minimum Viable Product — Weeks 1–2)

Achievable in 2 weeks:

- [x] Working testbed with veth namespaces
- [x] All 5 eBPF scripts producing valid CSV output
- [x] 4 baseline experiments (E1–E4) completed
- [x] Initial CDF plots showing runqueue delay differences under load
- [x] Validation that softirq colocation correlates with scheduling delay

This is demonstrable and already produces novel kernel-level insights.

### Stretch Goals

1. **Physical NIC experiments** — Repeat key experiments using a real Ethernet link between two machines to validate that veth findings generalize.
2. **nginx/wrk HTTP workload** — Add nginx + wrk as a secondary workload to compare web-server scheduling patterns against memcached's request–response model.
3. **EEVDF scheduler comparison** — On kernel 6.6+, compare the new EEVDF scheduler vs. CFS under the same conditions (EEVDF replaced CFS in mainline).
4. **Flamegraph integration** — Use `perf record -g` + FlameGraph tools to produce CPU flamegraphs showing where time is spent during high-delay periods.
5. **Automated anomaly detection** — eBPF script that triggers alerts when runqueue delay exceeds a threshold, with stack trace capture.
6. **XDP fast path** — Implement a minimal XDP program that handles a subset of packets before softirq, measuring skip-softirq benefit.

---

## 11. Risks and Fallback Plan

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **eBPF probes cause kernel panic** | Low | High | Test on disposable VM first. Use tracepoints (stable ABI) over kprobes where possible. Pin to known-good kernel version. |
| **veth doesn't generate realistic softirq load** | Medium | Medium | Validate with `perf top` that softirq% is measurable (>5%). If too low, increase packet rate with `pktgen` or use smaller packets (64B) to maximize pps. |
| **bpftrace version incompatibility** | Medium | Medium | Build from source if distro package is too old. Pin to bpftrace 0.17+. Fallback: use BCC Python tools instead. |
| **Insufficient CPU cores for isolation** | Medium | High | Use a cloud VM with ≥4 vCPUs (AWS c5.xlarge / GCP n2-standard-4). If only 2 cores: skip pinning experiments, focus on CFS + NAPI tuning. |
| **Trace data volume too large** | Low | Medium | Use in-kernel aggregation (histograms). Sample high-frequency events. Limit per-event logging to 1000 events/sec max. |
| **Non-reproducible results** | Medium | High | Run each experiment 3× minimum. Report mean ± stddev. Control for thermal throttling (check `dmesg`). Disable turbo boost: `echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo`. |
| **Kernel version too old for BTF** | Low | Medium | Fallback: use BCC with kernel headers instead of BTF. Or upgrade kernel: `apt install linux-image-6.1.0-*`. |
| **RPS steering on veth unstable** | Medium | Medium | If RPS steering on veth does not produce stable per-CPU separation, we will run the same core experiments on a physical NIC as a fallback validation set. Validate RPS effect by checking per-CPU `netif_receive_skb` distribution from `24_softirq_net.bt`. |

### Fallback Plan

If eBPF instrumentation proves unreliable:
1. **Fallback to `perf sched`**: `perf sched record` + `perf sched latency` provides runqueue delay without eBPF.
2. **Fallback to ftrace**: `/sys/kernel/debug/tracing/events/sched/` provides the same tracepoints via ftrace, readable as text.
3. **Fallback to `/proc` only**: All network metrics (`softnet_stat`, `snmp`, `sockstat`) and CPU metrics (`/proc/stat`) are always available without eBPF.

---

## Appendix: Quick Reference Commands

```bash
# Check eBPF/BTF support
ls /sys/kernel/btf/vmlinux && echo "BTF available"
bpftrace --info 2>&1 | head -20

# Check kernel version
uname -r

# Install dependencies (Ubuntu 22.04)
sudo apt update && sudo apt install -y \
    bpftrace bpfcc-tools linux-tools-$(uname -r) \
    iperf3 memcached libmemcached-tools stress-ng \
    python3-matplotlib python3-pandas python3-numpy

# Verify tracepoints exist
ls /sys/kernel/debug/tracing/events/sched/sched_wakeup
ls /sys/kernel/debug/tracing/events/irq/softirq_entry
ls /sys/kernel/debug/tracing/events/net/netif_receive_skb

# Quick sanity test
sudo bpftrace -e 'tracepoint:sched:sched_switch { @[comm] = count(); } interval:s:3 { exit(); }'

# Run experiment (example)
sudo ./24_run_experiment.sh --exp E4 --cpu-stress heavy --net-load high --duration 60 --runs 3
```

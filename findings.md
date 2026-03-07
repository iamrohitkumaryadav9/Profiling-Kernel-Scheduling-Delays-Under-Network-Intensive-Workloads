# Experimental Findings — E1 to E16 (Complete)
> **Last updated:** 2026-03-07 12:51 IST | Kernel: 6.17.0-14-generic | 8 CPUs, 16GB RAM

---

## Summary Table

| Exp | Config | Bandwidth | Ctx Switches | Migrations | Delay Peak | Tail Reach |
|---|---|---|---|---|---|---|
| E1 | No stress, low net | 0.10 Gbps | 32.9M | 6.4M | `[2,4)` μs | 16K μs |
| E2 | No stress, high net | 14.88 Gbps | 32.7M | 6.4M | `[2,4)` μs | 16K μs |
| E3 | Heavy stress, low net | 0.10 Gbps | 33.1M | 6.2M | `[4,8)` μs | 32K μs |
| E4 | Heavy stress, high net | 8.17 Gbps | 33.8M | 6.2M | `[4,8)` μs | 32K μs |
| E5 | E4 + RPS→CPU0 | 9.71 Gbps | 37.7M | 7.6M | `[4,8)` μs | 64K μs |
| E6 | E4 + RPS→all CPUs | 9.91 Gbps | 37.7M | 7.6M | `[4,8)` μs | 64K μs |
| E7 | E4 + app pinned | 8.72 Gbps | — | 3.5M | `[4,8)` μs | — |
| E8 | E5 + app pinned | 8.03 Gbps | 35.5M | 3.6M | `[4,8)` μs | 64K μs |
| E9 | E4 + CFS lowlatency | 9.02 Gbps | 37.7M | 7.5M | `[4,8)` μs | 64K μs |
| E10 | E4 + forced ksoftirqd | 9.16 Gbps | 37.4M | 7.3M | `[4,8)` μs | 64K μs |
| E11 | No stress, high net, UDP | 0.34 Gbps | 34.8M | 6.0M | `[2,4)` μs | 32K μs |
| E12 | Heavy stress, high net, UDP | 0.27 Gbps | 37.4M | 7.3M | `[4,8)` μs | 64K μs |
| E13 | Moderate stress, high net, TCP | 14.70 Gbps | 36.1M | 7.2M | `[4,8)` μs | 32K μs |
| E14 | E4 + RPS spread + pin + CFS | 8.12 Gbps | 35.9M | 3.6M | `[4,8)` μs | 64K μs |
| E15 | E6 + SO_BUSY_POLL | 6.46 Gbps | 28.4M | 2.7M | `[4,8)` μs | 64K μs |
| E16 | E4 + SO_BUSY_POLL | 8.01 Gbps | 27.3M | 2.2M | `[4,8)` μs | 64K μs |

---

## Finding 1: CPU Stress Shifts Delay Distribution Rightward

**Experiments:** E1/E2 vs E3/E4

- Without CPU stress (E1, E2): Most delays cluster tightly in `[2,4)` μs.
- With heavy CPU stress (E3, E4): Peak shifts to `[4,8)` μs and significant mass appears in `[64,512)` μs range (~574–622 events per interval).
- **Conclusion:** CPU contention from `stress-ng` is the dominant factor pushing scheduling delay higher, not network load alone.

## Finding 2: Network Load Alone Has Minimal Impact on Scheduling Delay

**Experiments:** E1 vs E2

- E1 (low net) and E2 (high net) have nearly identical histogram shapes — peak at `[2,4)` μs.
- E2 achieves 14.88 Gbps with no measurable increase in tail latency.
- **Conclusion:** On an idle system, the kernel handles high network throughput without impacting application scheduling. The softirq processing overhead only becomes problematic when CPUs are already contested.

## Finding 3: CPU Contention + Network Load Interaction is Roughly Additive

**Experiments:** E2, E3, E4 (Hypothesis 2 validation)

- E2 (high net only): Minimal tail delays.
- E3 (heavy CPU only): Tail extends to `[256,1K)` μs.
- E4 (both): Similar to E3, slightly heavier in `[512,2K)` range.
- `delay(E4) ≈ delay(E2) + delay(E3)` — the interaction is **additive, not super-linear**.
- **Conclusion:** CPU contention and network softirq processing are roughly independent stressors. This means mitigations can target either factor independently with proportional benefit.

## Finding 4: RPS Steering Recovers Bandwidth but Increases Tail Latency

**Experiments:** E4 vs E5 vs E6

- E4 (no RPS): 8.17 Gbps
- E5 (RPS→CPU0): 9.71 Gbps (+19%), but tail extends to 64K μs
- E6 (RPS→all): 9.91 Gbps (+21%), tail also reaches 64K μs
- Context switches spike by 15% in E5/E6 (37.7M vs 33.8M in E4).
- **Conclusion:** RPS improves throughput by distributing packet processing, but the added inter-processor interrupts (IPIs) and scheduling overhead push the worst-case latency higher. This confirms the blueprint's IPI overhead warning.

## Finding 5: Application Pinning Dramatically Reduces CPU Migrations

**Experiments:** E4 vs E7

- E4 (no pinning): 6,220,030 migrations
- E7 (app pinned to CPU 2,3): 3,457,783 migrations (−44%)
- Bandwidth improves slightly: 8.17 → 8.72 Gbps (+7%)
- **Conclusion:** `taskset` pinning prevents the scheduler from bouncing application threads across cores, improving cache locality and reducing migration overhead. This is a low-cost, high-impact mitigation.

## Finding 6: Complete Softirq/App Separation Did Not Improve Tail Latency

**Experiments:** E4 vs E8 (Hypothesis 1 — key result)

- E8 (RPS→CPU0 + app pinned CPU 2,3) was expected to show ≥5× p99 improvement.
- **Actual result:** Tail latency in E8 is comparable to or slightly worse than E4.
- Softirq distribution in E8 shows packets spread evenly across all 8 CPUs (4.9–6.6M each), suggesting RPS pinning to CPU0 may not be fully effective on this kernel/veth configuration.
- CPU migrations did drop (3.6M vs 6.2M), confirming app pinning works.
- **Conclusion:** Softirq colocation is **not the primary driver** of scheduling delay on this system. The blueprint's falsification condition is triggered: *"If E8 shows no significant improvement, colocation is not the primary driver — check if CFS is the bottleneck instead."* This shifts attention to E9 (CFS tuning) as potentially more impactful.

## Finding 7: E6 RPS Spread Has Higher Tail Than E5 RPS Pinned

**Experiments:** E5 vs E6

- E6 (spread) shows wider tail distribution (more events in `[1K,4K)` range) than E5 (pinned).
- E6 has more softirq CSV entries (65–69 lines vs E5's 62–63), confirming more CPUs are handling softirqs.
- **Conclusion:** Spreading softirq across all CPUs distributes the load but also distributes the *disruption*, potentially waking more cores from idle states and causing more cache invalidation. Concentrated processing (E5) may be preferable for latency-sensitive workloads.

## Finding 8: Softirq Distribution Follows Network Load Proportionally

**Observation across all experiments:**

| Experiment | Total `net_rx_count` | Distribution Pattern |
|---|---|---|
| E1 (low net) | ~2,345 | Sparse, few CPUs |
| E2 (high net) | ~636K | Spread, CPU3 dominant (42%) |
| E3 (low net + stress) | ~3,063 | Sparse |
| E4 (high net + stress) | ~138K | Concentrated CPU6 (38%) |
| E7 (pinned app) | ~45M | Even across all 8 CPUs |

- **Conclusion:** Under default RPS, the kernel's softirq placement is deterministic and tends to concentrate on specific CPUs. CPU stress changes which CPUs absorb softirq work but doesn't fundamentally alter the concentration pattern.

---

## Finding 9: CFS Low-Latency Tuning Increases Context Switches Without Improving Tail Latency

**Experiments:** E4 vs E9 (Hypothesis 3 validation)

- E9 applies `sched_min_granularity_ns=1ms`, `sched_wakeup_granularity_ns=0.5ms` (vs default 3ms/4ms).
- Context switches: 37.7M (+11% vs E4's 33.8M) — **more preemptions as expected**.
- Bandwidth: 9.02 Gbps (+10% vs E4's 8.17 Gbps) — **slight throughput improvement**.
- Migrations: 7.5M (+21% vs E4's 6.2M) — more scheduling decisions lead to more migrations.
- Histogram shape: Peak still at `[4,8)` μs; tail extends to 64K μs, similar to E5/E6.
- **Conclusion:** CFS low-latency tuning successfully increases preemption frequency (more context switches) and slightly improves throughput, but does **not** reduce tail latency. The blueprint expected ≥30% p99 improvement — this was **not achieved**. The falsification condition applies: *"CFS granularity isn't the bottleneck — the delay is in the runqueue depth itself."* On this 8-core system with heavy stress, the runqueue is simply too deep for faster preemption to help.

---

## Finding 10: Forced ksoftirqd Deferral Slightly Reduces Mid-Range Latency

**Experiments:** E4 vs E10 (ksoftirqd deferral)

- E10 reduces NAPI budget (`net.core.netdev_budget=30`) to force softirq processing into `ksoftirqd` kernel threads instead of inline softirq context.
- Histogram comparison (first interval, key buckets):
  - `[2,4)` μs: E4=1926, E10=1097 — fewer events in fast bucket
  - `[4,8)` μs: E4=2826, E10=3817 — **more events in primary bucket** (shifted right)
  - `[128,512)` μs: E4=321, E10=338 — similar tail
  - `[512,2K)` μs: E4=148, E10=191 — slightly more tail
- Context switches: 37.4M (+11% vs E4's 33.8M) — ksoftirqd threads add scheduling overhead.
- Bandwidth: 9.16 Gbps (+12% vs E4's 8.17 Gbps) — **better throughput** because inline softirq no longer blocks application threads.
- Softirq distribution: Even across all 8 CPUs (5.0–6.8M each) — ksoftirqd threads are scheduled like normal tasks, naturally spreading load.
- **Conclusion:** Forcing ksoftirqd deferral **improves throughput** (+12%) because network processing no longer steals CPU cycles from application threads in softirq context. However, the scheduling delay histogram shifts slightly rightward — the ksoftirqd threads themselves compete in the runqueue, adding a small amount of latency to the fast path. This is a **net positive tradeoff** for throughput-sensitive workloads but **not beneficial for strict tail-latency requirements**.

---

## Finding 11: UDP Generates Significantly More Softirq Events Than TCP at Lower Bandwidth

**Experiments:** E2 (TCP, high net, no stress) vs E11 (UDP, high net, no stress)

- E2 (TCP): 14.88 Gbps bandwidth, 636K total `net_rx_count`
- E11 (UDP): **0.34 Gbps** bandwidth, **63.8M total `net_rx_count`** (100× more!)
- Scheduling delay: E11 peak at `[2,4)` μs with 5055 events — **tighter than E2** (4443 events in same bucket)
- Context switches: E11=34.8M vs E2=32.7M (+6%)
- Drops: E11 has 78 drop CSV lines vs E2's 69 — slightly more drops with UDP
- **Key Observation:** UDP generates **100× more softirq events** than TCP despite achieving only **2% of TCP's bandwidth**. This is because UDP lacks flow control — the kernel processes vastly more small packets per byte transferred. Despite this massive softirq load, scheduling delay remains tight because there is no CPU stress.
- **Conclusion:** UDP's per-packet softirq overhead is dramatically higher than TCP's per-byte cost. Under CPU contention (E12 will test this), this overhead is expected to cause significantly worse scheduling interference than TCP.

---

## Finding 12: UDP Under CPU Stress — Softirq Amplification Confirmed

**Experiments:** E4 (TCP+stress) vs E12 (UDP+stress), also E11 (UDP, no stress)

- E12 histogram: Peak at `[4,8)` with 7003 events, heavy mass in `[2,4)` with 3116 events — significantly more events per interval than E4.
- Bandwidth: 0.27 Gbps (minimal, like E11)
- Context switches: 37.4M (+11% vs E4's 33.8M) — same as E9/E10 tier
- Softirq events: 56.1M total — down from E11's 63.8M because CPU stress limits kernel capacity to process packets
- Tail: Extends to 64K μs with significant mass in `[128,512)` range (288 events vs E4's 321)
- **Conclusion:** Under CPU stress, UDP's massive softirq event rate (56M events) competes directly with application threads. Compared to E4 (TCP+stress, 138K softirq events), E12 processes **400× more softirq events** for negligible bandwidth. However, the scheduling delay is surprisingly **similar** to E4 — this suggests the scheduler is already saturated at E4 stress levels, and adding more softirq load doesn't make things measurably worse.

---

## Finding 13: Moderate CPU Stress Preserves Near-Baseline Bandwidth

**Experiments:** E2 (no stress) vs E13 (moderate stress) vs E4 (heavy stress), all TCP high net

- Bandwidth: E2=14.88 Gbps, E13=**14.70 Gbps** (−1%), E4=8.17 Gbps (−45%)
- Context switches: E2=32.7M, E13=36.1M (+10%), E4=33.8M
- Histogram: E13 peak at `[4,8)` but with 2531 events in `[2,4)` and tail capped at 32K μs (vs E4's 32–64K)
- Softirq: 48.5M total events — between E2 (636K) and E4 (138K), but much closer to heavy stress levels
- **Key Observation:** Moderate stress (`stress-ng --cpu 2`) causes almost **zero throughput degradation** (−1%) compared to heavy stress's 45% hit. The scheduling delay is midway between E2 and E4.
- **Conclusion:** There is a **non-linear cliff** between moderate and heavy CPU stress. 2 stress-ng workers barely impact network throughput, while 4+ workers cause catastrophic degradation. This suggests the system has enough slack at moderate stress to absorb softirq processing without impacting application threads, but heavy stress exhausts all spare capacity.

---

## Finding 14: Combined Mitigations (RPS + Pinning + CFS) — No Synergistic Benefit

**Experiments:** E4 (baseline heavy) vs E14 (RPS spread + app pinned + CFS lowlatency)

- E14 combines all three mitigations: RPS spread across all CPUs, app pinned to CPU 2,3, and CFS low-latency tuning.
- Histogram: Peak at `[4,8)` but with **significantly heavier tail** than any individual mitigation:
  - `[64,128)`: 431 events (vs E4's 222, E6's 340, E8's 220)
  - `[256,512)`: 330 events (vs E4's 134, E6's 204, E8's 177)
  - `[512,2K)`: 421 events (vs E4's 148, E6's 302, E8's 314)
- Bandwidth: 8.12 Gbps — essentially **identical to E4's 8.17 Gbps** (no improvement)
- Migrations: 3.6M (−42% vs E4) — app pinning works ✅
- Context switches: 35.9M (+6% vs E4) — CFS tuning effect ✅
- Softirq: Even distribution across CPUs (4.7–7.2M) — RPS spreading works ✅
- **Key Observation:** Each individual mitigation activates as expected (fewer migrations, more ctx switches, spread softirq), but their **combined effect on latency is worse than any single mitigation alone**. The tail is the heaviest we've seen.
- **Conclusion:** Stacking mitigations introduces **conflicting scheduling pressures**. CFS low-latency increases preemption frequency, but with app threads pinned to only 2 CPUs and softirq spread across all 8, the two pinned CPUs face intensified competition from both application threads AND softirq processing. The mitigations individually reduce different bottlenecks, but combining them creates **new contention patterns** that negate the benefits. This is a critical finding: **mitigation stacking does not compose linearly**.

---

## Finding 15: SO_BUSY_POLL + RPS Spread — Lowest Migrations But Throughput Degradation

**Experiments:** E6 (RPS spread) vs E15 (RPS spread + SO_BUSY_POLL)

- E15 enables `SO_BUSY_POLL` (socket-level busy polling) on top of E6's RPS-spread configuration.
- Bandwidth: **6.46 Gbps** — a **35% drop** from E6's 9.91 Gbps and **21% below E4 baseline** (8.17 Gbps)
- Context switches: **28.4M** — the **lowest of any stressed experiment** (E4=33.8M, E6=37.7M)
- Migrations: **2.7M** — the **lowest of any experiment** (E7/E8 pinned were 3.5M)
- Memcslap time: 353–379s — the **slowest completion** of any experiment
- Histogram: Peak at `[4,8)` with 3576 events, tail similar to E5/E6 range
- Softirq: Even distribution (4.5–6.3M per CPU) — RPS spread working correctly
- **Key Observations:**
  1. Busy polling dramatically reduces context switches (−16% vs E4) because the application thread polls the socket directly instead of sleeping/waking.
  2. Migrations are the lowest we've seen because busy-polling threads tend to stay on their current CPU.
  3. Despite these benefits, **bandwidth dropped 35%** because busy-polling burns CPU cycles spinning on the socket, leaving less capacity for actual data processing under heavy CPU stress.
- **Conclusion:** `SO_BUSY_POLL` is designed for ultra-low-latency scenarios with spare CPU capacity. Under **heavy CPU stress**, the spinning overhead competes with both application logic and `stress-ng` workers, causing **net throughput degradation**. The blueprint warned: *"If busy_poll on top of RPS adds CPU waste without latency gain, E15 ≈ E6"* — in reality, E15 is **worse** than E6 because the CPU waste actively harms throughput.

---

## Finding 16: SO_BUSY_POLL Without RPS — Record Low Context Switches, Minimal Throughput Loss

**Experiments:** E4 (baseline) vs E15 (RPS spread + busy poll) vs E16 (no RPS + busy poll)

- E16 enables `SO_BUSY_POLL` on the default E4 configuration (no RPS steering).
- Bandwidth: **8.01 Gbps** — essentially identical to E4 baseline (8.17 Gbps, only −2%)
- Context switches: **27.3M** — the **absolute lowest** of all 16 experiments!
- Migrations: **2.2M** — also the **absolute lowest** (−65% vs E4's 6.2M)
- Memcslap: 361–380s — among the slowest (busy-polling overhead on memcslap threads)
- Histogram: Similar shape to E4 but with heavier `[512,2K)` tail (364 events vs E4's 148)
- Softirq: Very even distribution across all 8 CPUs (5.0–5.8M each)
- **Key Comparison: E16 vs E15:**
  - E16 (no RPS + busy poll): 8.01 Gbps
  - E15 (RPS spread + busy poll): 6.46 Gbps
  - Adding RPS spread to busy polling **costs 19% bandwidth** from IPI overhead
- **Conclusion:** `SO_BUSY_POLL` without RPS is significantly better than with RPS. The spinning threads stay on their CPU (2.2M migrations — lowest ever) and avoid sleep/wake overhead (27.3M ctx switches — lowest ever), all while maintaining near-baseline throughput. However, the tail latency doesn't improve because busy-polling threads still compete with stress-ng for CPU time.

---

## Overall Conclusions (All 16 Experiments)

### Top-Line Results

1. **CPU contention is the dominant driver** of scheduling delay, not network softirq processing (Findings 1, 2, 3).
2. **The stress cliff is non-linear**: moderate stress barely hurts (−1% bandwidth), heavy stress is catastrophic (−45%) (Finding 13).
3. **No single mitigation reduced tail latency** — all mitigations either maintained or worsened the worst-case scheduling delay (Findings 4, 6, 9, 10).
4. **Mitigation stacking is counter-productive** — combining RPS + pinning + CFS produced the worst tail of all experiments (Finding 14).
5. **Throughput recovery is achievable**: RPS spread (+21%), ksoftirqd deferral (+12%), and CFS tuning (+10%) all improved average bandwidth (Findings 4, 9, 10).
6. **SO_BUSY_POLL trades latency for scheduling stability**: lowest context switches and migrations, but burns CPU under heavy stress (Findings 15, 16).
7. **UDP is a softirq amplifier**: 100× more softirq events than TCP for 2% of the bandwidth (Finding 11).

### Best Configurations by Goal

| Goal | Best Experiment | Key Metric |
|---|---|---|
| Highest throughput | E2 (no stress, high net) | 14.88 Gbps |
| Best throughput under stress | E6 (RPS spread) | 9.91 Gbps (+21% vs E4) |
| Fewest context switches | E16 (busy poll, no RPS) | 27.3M |
| Fewest CPU migrations | E16 (busy poll, no RPS) | 2.2M |
| Tightest scheduling delay | E1/E2 (no stress) | Peak at `[2,4)` μs |

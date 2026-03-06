iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ mkdir -p /tmp/ebpf_test
sudo /usr/local/bin/bpftrace ~/Desktop/MT25037/ebpf_tools/24_sched_delay.bt > /tmp/ebpf_test/sched_delay.csv 2>/tmp/ebpf_test/sched_delay_hist.txt &
SCHED_PID=$!
sleep 5
sudo kill $SCHED_PID 2>/dev/null; wait $SCHED_PID 2>/dev/null
echo "=== CSV (first 5 lines) ===" && head -5 /tmp/ebpf_test/sched_delay.csv
echo "=== Histogram (last 30 lines) ===" && tail -30 /tmp/ebpf_test/sched_delay_hist.txt
[1] 612655
=== CSV (first 5 lines) ===
Attaching 7 probes...
timestamp_ns,pid,comm,cpu,delay_us,event
284516227504486,427992,kubelet,18,17,switch
284516362424750,422616,kube-controller,12,2,switch
284516557612033,421399,containerd,10,0,switch
=== Histogram (last 30 lines) ===
iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ sudo bash ~/Desktop/MT25037/scripts/24_setup_testbed.sh setup
sudo ip netns exec srv iperf3 -s -D
sudo /usr/local/bin/bpftrace ~/Desktop/MT25037/ebpf_tools/24_softirq_net.bt > /tmp/ebpf_test/softirq.csv 2>/tmp/ebpf_test/softirq_hist.txt &
SOFT_PID=$!
sudo ip netns exec cli iperf3 -c 10.0.0.1 -t 8 > /dev/null 2>&1
sleep 2
sudo kill $SOFT_PID 2>/dev/null; wait $SOFT_PID 2>/dev/null
echo "=== Softirq histogram (last 30 lines) ===" && tail -30 /tmp/ebpf_test/softirq_hist.txt
echo "=== Softirq CSV ===" && head -5 /tmp/ebpf_test/softirq.csv
[teardown] Cleaning up...
[teardown] Done.
[setup] Creating network namespaces...
[setup] ✓ Connectivity verified: cli → srv
[setup] ✓ Testbed ready
  Server namespace: srv (10.0.0.1)
  Client namespace: cli (10.0.0.2)

  Usage examples:
    ip netns exec srv iperf3 -s
    ip netns exec cli iperf3 -c 10.0.0.1 -t 60
    ip netns exec srv memcached -d -m 256 -t 2 -l 10.0.0.1 -u nobody
[1] 612877
=== Softirq histogram (last 30 lines) ===
=== Softirq CSV ===
Attaching 6 probes...
interval_s,cpu,net_rx_total_us,net_tx_total_us,wall_us,softirq_cpu_pct
22:51:30

--- NET_RX softirq per CPU ---
iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ sudo /usr/local/bin/bpftrace ~/Desktop/MT25037/ebpf_tools/24_net_drops.bt > /tmp/ebpf_test/drops.csv 2>/tmp/ebpf_test/drops_summary.txt &
DROP_PID=$!
sleep 5
sudo kill $DROP_PID 2>/dev/null; wait $DROP_PID 2>/dev/null
echo "=== Drops CSV ===" && head -5 /tmp/ebpf_test/drops.csv
echo "=== Drops summary ===" && cat /tmp/ebpf_test/drops_summary.txt
[1] 613092
=== Drops CSV ===
Attaching 6 probes...
timestamp_ns,event_type,cpu,pid,comm,detail
284548153091107,pkt_drop,0,16,ksoftirqd/0,loc=0xffffffff987dd641
284548165383029,pkt_drop,11,0,swapper/11,loc=0xffffffffc129b77b
284548205615091,pkt_drop,11,0,swapper/11,loc=0xffffffff987b2837
=== Drops summary ===
iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ sudo /usr/local/bin/bpftrace ~/Desktop/MT25037/ebpf_tools/24_cpu_migrations.bt > /tmp/ebpf_test/migrations.csv 2>/tmp/ebpf_test/migrations_summary.txt &
MIG_PID=$!
sleep 5
sudo kill $MIG_PID 2>/dev/null; wait $MIG_PID 2>/dev/null
echo "=== Migrations CSV (first 5) ===" && head -5 /tmp/ebpf_test/migrations.csv
echo "=== Migration summary ===" && tail -20 /tmp/ebpf_test/migrations_summary.txt
[1] 613241
=== Migrations CSV (first 5) ===
Attaching 4 probes...
timestamp_ns,pid,comm,orig_cpu,dest_cpu
284561925129242,421415,containerd,4,8
284561925133709,613244,bpftrace,8,14
284561925159207,421399,containerd,14,6
=== Migration summary ===
iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ sudo bash ~/Desktop/MT25037/ebpf_tools/24_proc_pollers.sh /tmp/ebpf_test/proc 5
echo "=== Files generated ===" && ls -la /tmp/ebpf_test/proc/
echo "=== CPU util (first 3 lines) ===" && head -3 /tmp/ebpf_test/proc/cpu_util.csv
echo "=== Softnet (first 3 lines) ===" && head -3 /tmp/ebpf_test/proc/softnet_stat.csv
echo "=== TCP stats (first 3 lines) ===" && head -3 /tmp/ebpf_test/proc/tcp_stats.csv
[proc_pollers] Collecting for 5s → /tmp/ebpf_test/proc
[proc_pollers] Started 5 pollers, PID=613339
[proc_pollers] Done.
=== Files generated ===
total 36
drwxr-xr-x 2 root  root  4096 Feb 16 22:52 .
drwxrwxr-x 3 iiitd iiitd 4096 Feb 16 22:52 ..
-rw-r--r-- 1 root  root  6526 Feb 16 22:52 cpu_util.csv
-rw-r--r-- 1 root  root   149 Feb 16 22:52 interrupts.csv
-rw-r--r-- 1 root  root   179 Feb 16 22:52 sockstat.csv
-rw-r--r-- 1 root  root  5699 Feb 16 22:52 softnet_stat.csv
-rw-r--r-- 1 root  root   250 Feb 16 22:52 tcp_stats.csv
=== CPU util (first 3 lines) ===
timestamp,cpu,user,nice,system,idle,iowait,irq,softirq,steal
1771262525.699690047,cpu0,89946,81,24481,28237887,8361,0,1241,0
1771262525.699690047,cpu1,71026,26,7123,28357209,1173,0,683,0
=== Softnet (first 3 lines) ===
timestamp,cpu_idx,processed,dropped,time_squeeze
1771262525.699441550,0,0x00251aa4,0x00000000,0x00000005
1771262525.699441550,1,0x00087d7c,0x00000000,0x00000000
=== TCP stats (first 3 lines) ===
timestamp,retrans_segs,in_segs,out_segs
1771262525.699659777,4910,4066498,4621754
1771262526.702480091,4910,4066558,4621813
iiitd@iiitd-ThinkCentre-M70s-Gen-3:~/Desktop/MT25037$ 
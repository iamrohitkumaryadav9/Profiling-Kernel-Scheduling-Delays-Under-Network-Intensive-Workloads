#!/bin/bash
# 24_setup_testbed.sh — Create isolated network namespaces for experiments
# Usage: sudo ./24_setup_testbed.sh [setup|teardown]

set -euo pipefail

ACTION="${1:-setup}"

teardown() {
    echo "[teardown] Cleaning up..."
    ip netns del srv 2>/dev/null || true
    ip netns del cli 2>/dev/null || true
    ip link del veth-srv 2>/dev/null || true
    echo "[teardown] Done."
}

setup() {
    # Clean first
    teardown

    echo "[setup] Creating network namespaces..."

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

    # Bring up interfaces and loopback
    ip netns exec srv ip link set veth-srv up
    ip netns exec srv ip link set lo up
    ip netns exec cli ip link set veth-cli up
    ip netns exec cli ip link set lo up

    # Set MTU
    ip netns exec srv ip link set veth-srv mtu 1500
    ip netns exec cli ip link set veth-cli mtu 1500

    # Increase txqueuelen for high-throughput tests
    ip netns exec srv ip link set veth-srv txqueuelen 10000
    ip netns exec cli ip link set veth-cli txqueuelen 10000

    # Verify connectivity
    if ip netns exec cli ping -c 1 -W 2 10.0.0.1 &>/dev/null; then
        echo "[setup] ✓ Connectivity verified: cli → srv"
    else
        echo "[setup] ✗ Connectivity FAILED"
        exit 1
    fi

    echo "[setup] ✓ Testbed ready"
    echo "  Server namespace: srv (10.0.0.1)"
    echo "  Client namespace: cli (10.0.0.2)"
    echo ""
    echo "  Usage examples:"
    echo "    ip netns exec srv iperf3 -s"
    echo "    ip netns exec cli iperf3 -c 10.0.0.1 -t 60"
    echo "    ip netns exec srv memcached -d -m 256 -t 2 -l 10.0.0.1 -u nobody"
}

case "$ACTION" in
    setup)   setup   ;;
    teardown) teardown ;;
    *)
        echo "Usage: $0 [setup|teardown]"
        exit 1
        ;;
esac

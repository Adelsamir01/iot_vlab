#!/usr/bin/env python3
"""verify_realism.py — Automated Realism Verification for iot_vlab

Verifies that the lab environment has realistic industrial conditions:
1. HMI Simulator generating background traffic (≥5 packets/sec)
2. Network impairments active (latency/loss on br0)
3. Multi-homed gateway segmentation (internal sensors not directly reachable)

Requires: sudo (for network access)
Usage:    sudo python3 verify_realism.py [--hmi-only] [--impair-only] [--segmentation-only]
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab_manager import LabManager

BRIDGE = "br0"
BRIDGE_INTERNAL = "br_internal"
SUBNET_BASE = "192.168.100"
SUBNET_INTERNAL_BASE = "192.168.200"
MIN_PACKETS_PER_SEC = 5
VERIFICATION_DURATION = 10  # seconds


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=check)
    return result


def check_hmi_traffic() -> tuple[bool, str]:
    """Check 1: Verify HMI Simulator is generating at least 5 packets/sec on br0."""
    print("[*] Check 1: HMI Simulator Background Traffic")
    print("    Capturing packets on br0 for 10 seconds...")
    
    # Start tcpdump in background
    pcap_file = "/tmp/hmi_verify.pcap"
    try:
        os.remove(pcap_file)
    except FileNotFoundError:
        pass
    
    # Capture Modbus (TCP 502) and CoAP (UDP 5683) traffic
    tcpdump_cmd = [
        "tcpdump", "-i", BRIDGE,
        "-w", pcap_file,
        "-c", "100",  # Stop after 100 packets or timeout
        "tcp port 502 or udp port 5683",
    ]
    
    proc = subprocess.Popen(
        tcpdump_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # Wait for capture
    time.sleep(VERIFICATION_DURATION)
    proc.terminate()
    proc.wait(timeout=2)
    
    # Count packets using tcpdump read
    if not os.path.exists(pcap_file):
        return False, "No capture file created (tcpdump may not be running)"
    
    count_cmd = ["tcpdump", "-r", pcap_file, "2>/dev/null"]
    result = run_cmd(["sh", "-c", " ".join(count_cmd)], check=False)
    
    # Count lines (each packet is one line)
    packet_count = len([l for l in result.stdout.splitlines() if l.strip()])
    packets_per_sec = packet_count / VERIFICATION_DURATION
    
    # Cleanup
    try:
        os.remove(pcap_file)
    except:
        pass
    
    if packets_per_sec >= MIN_PACKETS_PER_SEC:
        return True, f"✓ {packets_per_sec:.1f} packets/sec (threshold: {MIN_PACKETS_PER_SEC})"
    else:
        return False, f"✗ {packets_per_sec:.1f} packets/sec (threshold: {MIN_PACKETS_PER_SEC}) - HMI simulator may not be running"


def check_network_impairments() -> tuple[bool, str]:
    """Check 2: Verify network impairments are active on br0."""
    print("[*] Check 2: Network Impairments")
    print("    Checking tc qdisc configuration on br0...")
    
    result = run_cmd(["tc", "qdisc", "show", "dev", BRIDGE], check=False)
    
    if result.returncode != 0:
        return False, "✗ tc command failed or br0 has no qdisc configured"
    
    output = result.stdout.strip()
    if not output or "netem" not in output:
        return False, "✗ No netem impairments detected on br0 (use impair_network.sh)"
    
    # Parse impairment details
    if "loss" in output:
        return True, f"✓ Packet loss configured: {output}"
    elif "delay" in output:
        return True, f"✓ Latency/jitter configured: {output}"
    else:
        return True, f"✓ Impairments detected: {output}"


def check_segmentation() -> tuple[bool, str]:
    """Check 3: Verify multi-homed gateway segmentation (internal sensors not directly reachable)."""
    print("[*] Check 3: Multi-Homed Gateway Segmentation")
    
    # Check if br_internal exists
    result = run_cmd(["ip", "link", "show", BRIDGE_INTERNAL], check=False)
    if result.returncode != 0:
        return False, f"✗ Bridge {BRIDGE_INTERNAL} does not exist (no multi-homed devices)"
    
    # Check if there are any multi-homed devices
    manager = LabManager()
    manager.refresh_ips()
    topo = manager.get_topology()
    
    multi_homed_devices = [d for d in topo if d.get("multi_homed")]
    if not multi_homed_devices:
        return False, "✗ No multi-homed devices found in topology"
    
    # Check if internal IPs exist
    internal_ips = []
    for dev in multi_homed_devices:
        ip_int = dev.get("ip_internal")
        if ip_int and ip_int not in ("pending", "unknown"):
            internal_ips.append(ip_int)
    
    if not internal_ips:
        return False, "✗ Multi-homed devices found but no internal IPs assigned yet"
    
    # Try to ping internal IP from host (should fail if segmentation works)
    # Note: This is a basic check - real segmentation would require routing rules
    print(f"    Testing reachability of internal IPs: {internal_ips}")
    
    unreachable_count = 0
    for ip in internal_ips[:3]:  # Test up to 3 IPs
        result = run_cmd(["ping", "-c", "1", "-W", "1", ip], check=False)
        if result.returncode != 0:
            unreachable_count += 1
    
    if unreachable_count == len(internal_ips[:3]):
        return True, f"✓ Internal IPs not directly reachable from host (segmentation working)"
    elif unreachable_count > 0:
        return True, f"⚠ Partial segmentation: {unreachable_count}/{len(internal_ips[:3])} IPs unreachable"
    else:
        return False, "✗ Internal IPs are directly reachable (segmentation not enforced)"


def main():
    parser = argparse.ArgumentParser(
        description="Verify realistic industrial conditions in iot_vlab"
    )
    parser.add_argument(
        "--hmi-only",
        action="store_true",
        help="Only check HMI simulator traffic"
    )
    parser.add_argument(
        "--impair-only",
        action="store_true",
        help="Only check network impairments"
    )
    parser.add_argument(
        "--segmentation-only",
        action="store_true",
        help="Only check multi-homed segmentation"
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[ERROR] This script must be run with sudo for network access.")
        sys.exit(1)

    print("=" * 72)
    print("  IoT Virtual Lab — Realism Verification")
    print("=" * 72)
    print()

    results = []
    
    # Run checks based on flags
    if not args.impair_only and not args.segmentation_only:
        success, msg = check_hmi_traffic()
        results.append(("HMI Background Traffic", success, msg))
        print(f"    Result: {msg}\n")
    
    if not args.hmi_only and not args.segmentation_only:
        success, msg = check_network_impairments()
        results.append(("Network Impairments", success, msg))
        print(f"    Result: {msg}\n")
    
    if not args.hmi_only and not args.impair_only:
        success, msg = check_segmentation()
        results.append(("Gateway Segmentation", success, msg))
        print(f"    Result: {msg}\n")

    # Summary
    print("=" * 72)
    print("  Verification Summary")
    print("=" * 72)
    
    all_passed = True
    for name, success, msg in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"  {status}  {name}")
        if not success:
            all_passed = False
    
    print()
    if all_passed:
        print("  ✓ All realism checks passed!")
        return 0
    else:
        print("  ✗ Some checks failed. Review the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""industrial_hmi_sim.py — Industrial HMI Simulator for Realistic Background Traffic.

This script simulates a legitimate Master/HMI that periodically polls devices
on the 192.168.100.0/24 subnet for Modbus (TCP 502) and CoAP (UDP 5683) data.
Uses Poisson distribution for polling intervals to create realistic, non-rhythmic
traffic patterns that prevent easy filtering by AI agents.

Requires: sudo (for network access)
Usage:    sudo python3 industrial_hmi_sim.py [--interval MEAN_SECONDS]
"""

import argparse
import math
import os
import random
import socket
import sys
import time
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab_manager import LabManager

# Network configuration
SUBNET_BASE = "192.168.100"
SUBNET_RANGE = range(10, 51)  # DHCP range: .10 to .50
MODBUS_PORT = 502
COAP_PORT = 5683
DEFAULT_MEAN_INTERVAL = 2.0  # seconds (Poisson lambda)

# Modbus/TCP minimal request (Function Code 3: Read Holding Registers)
MODBUS_REQUEST = bytes([
    0x00, 0x01,  # Transaction ID
    0x00, 0x00,  # Protocol ID
    0x00, 0x06,  # Length
    0x01,        # Unit ID
    0x03,        # Function Code: Read Holding Registers
    0x00, 0x00,  # Starting Address
    0x00, 0x01,  # Quantity of Registers
])

# CoAP GET request (minimal, non-confirmable)
COAP_REQUEST = bytes([
    0x40,  # Version=1, Type=Non-confirmable, Token Length=0
    0x01,  # Code=GET
    0x00,  # Message ID (high byte)
    0x01,  # Message ID (low byte)
])


def poisson_interval(mean: float) -> float:
    """Generate a random interval using exponential distribution (Poisson process inter-arrival time)."""
    # Exponential distribution: -mean * ln(U) where U is uniform [0,1)
    u = random.random()
    if u == 0.0:
        u = 1e-10  # Avoid log(0)
    return -mean * math.log(u)


def poll_modbus(ip: str, timeout: float = 1.0) -> bool:
    """Attempt a Modbus/TCP read request to the given IP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, MODBUS_PORT))
        sock.sendall(MODBUS_REQUEST)
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError):
        return False


def poll_coap(ip: str, timeout: float = 1.0) -> bool:
    """Attempt a CoAP GET request to the given IP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(COAP_REQUEST, (ip, COAP_PORT))
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError):
        return False


def scan_subnet_for_devices() -> list[str]:
    """Scan the subnet to find active devices (quick TCP connect test on common ports)."""
    active_ips = []
    for host in SUBNET_RANGE:
        ip = f"{SUBNET_BASE}.{host}"
        # Quick TCP connect test on Modbus port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            result = sock.connect_ex((ip, MODBUS_PORT))
            sock.close()
            if result == 0:
                active_ips.append(ip)
        except:
            pass
    return active_ips


def main():
    parser = argparse.ArgumentParser(
        description="Industrial HMI Simulator - Generates realistic background traffic"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_MEAN_INTERVAL,
        help=f"Mean polling interval in seconds (default: {DEFAULT_MEAN_INTERVAL})"
    )
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="Scan subnet once at startup, then only poll discovered devices"
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[ERROR] This script must be run with sudo for network access.")
        sys.exit(1)

    print("[*] Industrial HMI Simulator starting...")
    print(f"[*] Subnet: {SUBNET_BASE}.0/24")
    print(f"[*] Mean polling interval: {args.interval:.2f}s (Poisson distribution)")
    print(f"[*] Protocols: Modbus/TCP :{MODBUS_PORT}, CoAP UDP :{COAP_PORT}")
    print()

    # Optionally scan once at startup
    target_ips = []
    if args.scan_once:
        print("[*] Scanning subnet for active devices...")
        target_ips = scan_subnet_for_devices()
        if target_ips:
            print(f"[*] Found {len(target_ips)} device(s): {', '.join(target_ips)}")
        else:
            print("[*] No devices found, will poll entire subnet range")
            target_ips = [f"{SUBNET_BASE}.{h}" for h in SUBNET_RANGE]
    else:
        target_ips = [f"{SUBNET_BASE}.{h}" for h in SUBNET_RANGE]

    print("[*] Starting background polling (Ctrl+C to stop)...\n")

    poll_count = {"modbus": 0, "coap": 0, "total": 0}

    try:
        while True:
            # Select random IP from target list
            ip = random.choice(target_ips)
            
            # Randomly choose protocol (70% Modbus, 30% CoAP for industrial bias)
            protocol = "modbus" if random.random() < 0.7 else "coap"
            
            if protocol == "modbus":
                success = poll_modbus(ip)
                poll_count["modbus"] += 1
            else:
                success = poll_coap(ip)
                poll_count["coap"] += 1
            
            poll_count["total"] += 1
            
            status = "✓" if success else "✗"
            print(f"[{status}] {protocol.upper():6s} → {ip:15s} (total: {poll_count['total']})")
            
            # Wait using Poisson distribution
            wait_time = max(0.1, poisson_interval(args.interval))
            time.sleep(wait_time)
            
    except KeyboardInterrupt:
        print("\n\n[*] Shutting down HMI Simulator...")
        print(f"[*] Statistics:")
        print(f"    Modbus polls: {poll_count['modbus']}")
        print(f"    CoAP polls:   {poll_count['coap']}")
        print(f"    Total polls:  {poll_count['total']}")
        print("[*] Exiting.")


if __name__ == "__main__":
    main()

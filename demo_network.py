#!/usr/bin/env python3
"""demo_network.py — Spawn a multi-architecture IoT network and display its topology.

This script demonstrates the IoT Cyber Range by booting several virtual
devices across different CPU architectures, all connected to the same
virtual bridge (br0).  It prints a live topology map and waits for you
to press Ctrl+C before tearing everything down.

Requires: sudo (for TAP interface management and QEMU networking).
Usage:    sudo python3 demo_network.py
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab_manager import LabManager
from scan_library import scan

# ── Configuration ────────────────────────────────────────────────────────
# Define the network: each entry is a firmware_id from the library and a
# human-readable role label.  Add or remove entries to change the network.
NETWORK = [
    {"firmware_id": "dvrf_v03",       "role": "Vulnerable Router"},
    {"firmware_id": "dvrf_v03",       "role": "IoT Gateway"},
    {"firmware_id": "debian_armel",   "role": "ARM Sensor Node"},
    {"firmware_id": "debian_armel",   "role": "ARM Camera"},
    {"firmware_id": "zephyr_coap",    "role": "Smart Meter (CoAP)"},
]

BOOT_WAIT = 10          # seconds to wait for DHCP leases after spawning
IP_POLL_INTERVAL = 5    # seconds between IP refresh attempts
IP_POLL_ROUNDS = 6      # how many times to poll before giving up


# ── Topology display ────────────────────────────────────────────────────

ARCH_LABELS = {
    "mipsel":    "MIPS32 Little-Endian",
    "armel":     "ARMv5 Little-Endian",
    "cortex-m3": "ARM Cortex-M3 (Zephyr)",
    "riscv32":   "RISC-V 32-bit",
}

def print_header():
    print("\033[2J\033[H", end="")  # clear screen
    print("=" * 72)
    print("  IoT Cyber Range — Multi-Architecture Network Demo")
    print("=" * 72)


def print_library():
    """Show every firmware available in the library with specs."""
    firmwares = scan()
    print("\n  Available Firmware Library")
    print("  " + "-" * 52)
    for fw in firmwares:
        arch_nice = ARCH_LABELS.get(fw["arch"], fw["arch"])
        print(f"  [{fw['id']}]")
        print(f"    Name : {fw['name']}")
        print(f"    Arch : {arch_nice}")
        print(f"    Board: {fw.get('qemu_machine', 'N/A')}")
        print(f"    Creds: {fw.get('default_creds', 'N/A')}")
    print()


def print_topology(manager: LabManager, roles: dict[str, str]):
    """Print an ASCII topology diagram of the running network."""
    manager.refresh_ips()
    topo = manager.get_topology()

    print("\n  Live Network Topology")
    print("  " + "-" * 52)

    # Bridge header
    print("""
                    ┌──────────────────────────┐
                    │  br0  192.168.100.1/24    │
                    │  DHCP  .10 — .50          │
                    │  NAT → host default iface │
                    └─────────┬────────────────┘
                              │""")

    if not topo:
        print("                         (no devices)")
    else:
        for i, dev in enumerate(topo):
            role = roles.get(dev["id"], "Device")
            arch_nice = ARCH_LABELS.get(dev["arch"], dev["arch"])
            status = "\033[92mRUNNING\033[0m" if dev["alive"] else "\033[91mDEAD\033[0m"
            ip_str = dev["ip"] if dev["ip"] not in ("pending", "unknown") else "\033[93mwaiting...\033[0m"
            is_last = (i == len(topo) - 1)
            connector = "└" if is_last else "├"
            pipe      = " " if is_last else "│"

            print(f"                              │")
            print(f"                    {connector}─── [{dev['tap']}] ─── ┌─────────────────────────────┐")
            print(f"                    {pipe}                  │ {role:<28s}│")
            print(f"                    {pipe}                  │  ID   : {dev['id']:<20s}│")
            print(f"                    {pipe}                  │  Arch : {arch_nice:<20s}│")
            print(f"                    {pipe}                  │  MAC  : {dev['mac']:<20s}│")
            print(f"                    {pipe}                  │  IP   : {ip_str:<20s}│" if dev["ip"] not in ("pending","unknown") else f"                    {pipe}                  │  IP   : waiting...           │")
            print(f"                    {pipe}                  │  PID  : {dev['pid']:<20d}│")
            print(f"                    {pipe}                  │  State: {status}                │")
            print(f"                    {pipe}                  └─────────────────────────────┘")

    print()


def print_device_table(manager: LabManager, roles: dict[str, str]):
    """Print a compact summary table."""
    manager.refresh_ips()
    topo = manager.get_topology()

    hdr = f"  {'Role':<20s} {'Arch':<22s} {'TAP':<6s} {'MAC':<19s} {'IP':<17s} {'PID':<8s} {'State':<7s}"
    print("  Device Summary")
    print("  " + "-" * 100)
    print(hdr)
    print("  " + "-" * 100)
    for dev in topo:
        role = roles.get(dev["id"], "Device")
        arch_nice = ARCH_LABELS.get(dev["arch"], dev["arch"])
        ip_str = dev["ip"] if dev["ip"] not in ("pending", "unknown") else "waiting..."
        state = "UP" if dev["alive"] else "DOWN"
        print(f"  {role:<20s} {arch_nice:<22s} {dev['tap']:<6s} {dev['mac']:<19s} {ip_str:<17s} {dev['pid']:<8d} {state:<7s}")
    print("  " + "-" * 100)
    print(f"  Total: {len(topo)} device(s)")
    print()


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Spawn a multi-architecture IoT network with optional HMI simulator"
    )
    parser.add_argument(
        "--hmi",
        action="store_true",
        help="Start Industrial HMI Simulator for background traffic"
    )
    parser.add_argument(
        "--hmi-interval",
        type=float,
        default=2.0,
        help="Mean polling interval for HMI simulator (default: 2.0 seconds)"
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[ERROR] This script must be run with sudo.")
        sys.exit(1)

    # Check that firmware files exist
    available = {fw["id"] for fw in scan()}
    for entry in NETWORK:
        if entry["firmware_id"] not in available:
            print(f"[ERROR] Firmware '{entry['firmware_id']}' not in library.")
            print("        Run ./download_firmware.sh first.")
            sys.exit(1)

    manager = LabManager()
    roles: dict[str, str] = {}  # run_id → role label
    hmi_process = None

    # Start HMI simulator if requested
    if args.hmi:
        print("[*] Starting Industrial HMI Simulator...")
        script_path = Path(__file__).resolve().parent / "industrial_hmi_sim.py"
        hmi_process = subprocess.Popen(
            [sys.executable, str(script_path), "--interval", str(args.hmi_interval)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print("[*] HMI Simulator started (PID: {})".format(hmi_process.pid))
        time.sleep(1)  # Give it a moment to start

    # Clean shutdown on Ctrl+C
    def shutdown(sig, frame):
        print("\n\n[*] Shutting down all devices...")
        manager.reset_lab()
        if hmi_process:
            print("[*] Stopping HMI Simulator...")
            hmi_process.terminate()
            try:
                hmi_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                hmi_process.kill()
        print("[*] All devices stopped. Network clean.")
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print_header()
    print_library()

    # ── Spawn all devices ────────────────────────────────────────────────
    print("  Spawning devices...\n")
    for entry in NETWORK:
        fid = entry["firmware_id"]
        role = entry["role"]
        try:
            run_id = manager.spawn_instance(fid)
            roles[run_id] = role
            print(f"  [+] Spawned: {role:<22s} ({fid}, run_id={run_id})")
        except Exception as exc:
            print(f"  [!] FAILED: {role} — {exc}")

    # ── Wait for IPs ─────────────────────────────────────────────────────
    print(f"\n  Waiting {BOOT_WAIT}s for devices to boot and request DHCP leases...")
    time.sleep(BOOT_WAIT)

    for attempt in range(IP_POLL_ROUNDS):
        manager.refresh_ips()
        topo = manager.get_topology()
        pending = [d for d in topo if d["ip"] in ("pending", "unknown")]
        if not pending:
            break
        print(f"  ... {len(pending)} device(s) still waiting for IP (attempt {attempt+1}/{IP_POLL_ROUNDS})")
        time.sleep(IP_POLL_INTERVAL)

    # ── Display topology ─────────────────────────────────────────────────
    print_header()
    print_library()
    print_topology(manager, roles)
    print_device_table(manager, roles)

    if args.hmi:
        print("  [*] Industrial HMI Simulator is running (background traffic active)")
    print("  Press Ctrl+C to tear down the network and exit.\n")

    # ── Keep alive ───────────────────────────────────────────────────────
    while True:
        time.sleep(5)
        # Refresh IPs in background in case a late lease appears
        manager.refresh_ips()


if __name__ == "__main__":
    main()

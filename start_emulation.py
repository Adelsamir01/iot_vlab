#!/usr/bin/env python3
"""start_emulation.py — QEMU Emulation Controller for IoT Lab.

Boots firmware images in QEMU with TAP networking attached to br0.
Supports MIPS (little-endian) and ARM architectures.
"""

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG_DIR = Path.home() / "iot-lab" / "logs"
LEASE_FILE = Path("/var/lib/misc/dnsmasq-br0.leases")
BRIDGE = "br0"

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger("emulation")


def run(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    """Run a command, logging it first."""
    log.debug("exec: %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True, **kw)


@dataclass
class QemuInstance:
    """Manages a single QEMU guest lifecycle."""

    arch: str  # "mips" or "arm"
    kernel: Path
    rootfs: Path
    tap_name: str = "tap0"
    bridge: str = BRIDGE
    mac: str = "52:54:00:12:34:56"
    _process: Optional[subprocess.Popen] = field(default=None, repr=False, init=False)
    guest_ip: Optional[str] = field(default=None, repr=False, init=False)

    # -- Networking helpers --------------------------------------------------

    def _tap_exists(self) -> bool:
        r = run(["ip", "link", "show", self.tap_name], check=False)
        return r.returncode == 0

    def setup_tap(self) -> None:
        """Create TAP interface and attach it to the bridge."""
        if self._tap_exists():
            log.info("TAP %s already exists — reusing.", self.tap_name)
            # Ensure it's attached to bridge
            run(["sudo", "ip", "link", "set", self.tap_name, "master", self.bridge], check=False)
            run(["sudo", "ip", "link", "set", self.tap_name, "up"])
            return

        log.info("Creating TAP %s ...", self.tap_name)
        run(["sudo", "ip", "tuntap", "add", "dev", self.tap_name, "mode", "tap"])
        run(["sudo", "ip", "link", "set", self.tap_name, "master", self.bridge])
        run(["sudo", "ip", "link", "set", self.tap_name, "up"])
        log.info("TAP %s attached to bridge %s.", self.tap_name, self.bridge)

    def teardown_tap(self) -> None:
        """Remove TAP interface."""
        if self._tap_exists():
            log.info("Removing TAP %s ...", self.tap_name)
            run(["sudo", "ip", "link", "set", self.tap_name, "down"], check=False)
            run(["sudo", "ip", "tuntap", "del", "dev", self.tap_name, "mode", "tap"], check=False)

    # -- QEMU command construction -------------------------------------------

    def _build_cmd(self) -> list[str]:
        if self.arch == "mips":
            qemu_bin = "qemu-system-mipsel"
            machine = "malta"
            append = "root=/dev/sda1 console=ttyS0"
            drive_arg = ["-drive", f"file={self.rootfs},format=qcow2"]
        elif self.arch == "arm":
            qemu_bin = "qemu-system-arm"
            machine = "virt"
            append = "root=/dev/vda1 console=ttyAMA0"
            drive_arg = ["-drive", f"file={self.rootfs},format=raw,if=virtio"]
        else:
            raise ValueError(f"Unsupported architecture: {self.arch}")

        cmd = [
            qemu_bin,
            "-M", machine,
            "-kernel", str(self.kernel),
            *drive_arg,
            "-nographic",
            "-append", append,
            "-m", "256",
            "-netdev", f"tap,id=net0,ifname={self.tap_name},script=no,downscript=no",
            "-device", f"e1000,netdev=net0,mac={self.mac}",
        ]
        return cmd

    # -- Guest IP discovery --------------------------------------------------

    def _find_guest_ip(self, timeout: int = 60) -> Optional[str]:
        """Poll dnsmasq leases for our MAC address."""
        log.info("Waiting for guest to acquire DHCP lease (timeout %ds)...", timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if LEASE_FILE.exists():
                for line in LEASE_FILE.read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[1].lower() == self.mac.lower():
                        ip = parts[2]
                        log.info("Guest IP found: %s", ip)
                        return ip
            time.sleep(3)
        log.warning("Guest did not acquire a DHCP lease within %ds.", timeout)
        return None

    # -- Lifecycle -----------------------------------------------------------

    def start(self, wait_ip: bool = True, ip_timeout: int = 90) -> Optional[str]:
        """Boot the QEMU instance and optionally wait for an IP."""
        # Validate files
        if not self.kernel.is_file():
            log.error("Kernel not found: %s", self.kernel)
            sys.exit(1)
        if not self.rootfs.is_file():
            log.error("Rootfs not found: %s", self.rootfs)
            sys.exit(1)

        self.setup_tap()
        cmd = self._build_cmd()
        log.info("Starting QEMU: %s", " ".join(cmd))

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stdout_log = open(LOG_DIR / f"qemu-{self.tap_name}.log", "w")

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_log,
            stderr=subprocess.STDOUT,
        )
        log.info("QEMU started (PID %d). Console log: %s", self._process.pid, stdout_log.name)

        if wait_ip:
            self.guest_ip = self._find_guest_ip(timeout=ip_timeout)
            if self.guest_ip:
                # Quick connectivity check
                r = run(["ping", "-c", "2", "-W", "2", self.guest_ip], check=False)
                if r.returncode == 0:
                    log.info("Guest %s is reachable via ping.", self.guest_ip)
                else:
                    log.warning("Guest %s not responding to ping yet.", self.guest_ip)
            return self.guest_ip
        return None

    def stop(self) -> None:
        """Gracefully stop QEMU and clean up networking."""
        if self._process and self._process.poll() is None:
            log.info("Sending SIGTERM to QEMU (PID %d)...", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("QEMU did not exit, sending SIGKILL...")
                self._process.kill()
                self._process.wait()
            log.info("QEMU stopped.")
        self.teardown_tap()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


def main() -> None:
    parser = argparse.ArgumentParser(description="IoT Lab QEMU Emulation Controller")
    parser.add_argument(
        "--arch", choices=["mips", "arm"], default="mips",
        help="Guest architecture (default: mips)",
    )
    parser.add_argument(
        "--kernel", type=Path,
        default=Path.home() / "iot-lab/firmware/vmlinux-3.2.0-4-4kc-malta",
        help="Path to kernel image",
    )
    parser.add_argument(
        "--rootfs", type=Path,
        default=Path.home() / "iot-lab/firmware/rootfs.img",
        help="Path to root filesystem image",
    )
    parser.add_argument(
        "--tap", default="tap0",
        help="TAP interface name (default: tap0)",
    )
    parser.add_argument(
        "--mac", default="52:54:00:12:34:56",
        help="Guest MAC address",
    )
    parser.add_argument(
        "--timeout", type=int, default=90,
        help="Seconds to wait for guest DHCP lease (default: 90)",
    )
    args = parser.parse_args()

    instance = QemuInstance(
        arch=args.arch,
        kernel=args.kernel,
        rootfs=args.rootfs,
        tap_name=args.tap,
        mac=args.mac,
    )

    # Handle clean shutdown
    def _shutdown(sig, frame):
        log.info("Caught signal %s — shutting down...", sig)
        instance.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    guest_ip = instance.start(wait_ip=True, ip_timeout=args.timeout)

    if guest_ip:
        print(f"\n{'='*45}")
        print(f"  Guest booted successfully!")
        print(f"  IP Address : {guest_ip}")
        print(f"  Architecture: {args.arch}")
        print(f"  TAP        : {args.tap}")
        print(f"{'='*45}\n")
        print("Press Ctrl+C to stop the emulation.")
        # Keep running until interrupted
        while instance.is_running:
            time.sleep(2)
    else:
        log.error("Guest failed to obtain an IP. Check logs at %s", LOG_DIR)
        instance.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()

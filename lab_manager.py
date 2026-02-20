#!/usr/bin/env python3
"""lab_manager.py — Multi-device QEMU hypervisor for the IoT Cyber Range."""

import json
import logging
import os
import random
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from scan_library import LIBRARY_DIR, scan

BRIDGE = "br0"
BRIDGE_INTERNAL = "br_internal"
LOG_DIR = Path(__file__).resolve().parent / "logs"
OVERLAY_DIR = Path(__file__).resolve().parent / "overlays"
LEASE_FILE = Path("/var/lib/misc/dnsmasq-br0.leases")
LEASE_FILE_INTERNAL = Path("/var/lib/misc/dnsmasq-br_internal.leases")

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("lab_manager")


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


class LabManager:
    """Manages multiple concurrent QEMU instances with dynamic networking."""

    def __init__(self) -> None:
        self.active_instances: dict[str, dict] = {}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _generate_mac() -> str:
        """Generate a random unicast MAC in the QEMU OUI range."""
        return "52:54:00:{:02x}:{:02x}:{:02x}".format(
            random.randint(0, 0xFF),
            random.randint(0, 0xFF),
            random.randint(0, 0xFF),
        )

    def _get_next_tap(self, suffix: str = "") -> str:
        """Find the lowest unused tap<N> interface."""
        existing = set()
        net_dir = Path("/sys/class/net")
        if net_dir.is_dir():
            for iface in net_dir.iterdir():
                name = iface.name
                if name.startswith("tap") and name[3:].isdigit():
                    existing.add(int(name[3:]))
        idx = 0
        while idx in existing:
            idx += 1
        return f"tap{idx}{suffix}"

    @staticmethod
    def _create_tap(tap: str, bridge: str = BRIDGE) -> None:
        """Create a TAP interface and attach it to the specified bridge."""
        _run(["sudo", "ip", "tuntap", "add", "dev", tap, "mode", "tap"])
        _run(["sudo", "ip", "link", "set", tap, "master", bridge])
        _run(["sudo", "ip", "link", "set", tap, "up"])
    
    @staticmethod
    def _ensure_internal_bridge() -> None:
        """Ensure br_internal bridge exists and is configured."""
        # Check if bridge exists
        result = _run(["ip", "link", "show", BRIDGE_INTERNAL], check=False)
        if result.returncode != 0:
            log.info("Creating internal bridge %s...", BRIDGE_INTERNAL)
            _run(["sudo", "ip", "link", "add", "name", BRIDGE_INTERNAL, "type", "bridge"])
            # Assign IP to internal bridge (different subnet: 192.168.200.0/24)
            _run(["sudo", "ip", "addr", "add", "192.168.200.1/24", "dev", BRIDGE_INTERNAL])
            _run(["sudo", "ip", "link", "set", BRIDGE_INTERNAL, "up"])
            log.info("Internal bridge %s created and configured", BRIDGE_INTERNAL)

    @staticmethod
    def _destroy_tap(tap: str) -> None:
        _run(["sudo", "ip", "link", "set", tap, "down"], check=False)
        _run(["sudo", "ip", "tuntap", "del", "dev", tap, "mode", "tap"], check=False)

    def _load_firmware(self, firmware_id: str) -> dict:
        """Load a firmware config from the library."""
        for fw in scan():
            if fw["id"] == firmware_id:
                return fw
        raise ValueError(f"Firmware '{firmware_id}' not found in library")

    @staticmethod
    def _create_overlay(base_rootfs: str, run_id: str) -> str:
        """Create a qcow2 copy-on-write overlay so multiple VMs can
        share the same base image without write-lock conflicts."""
        OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
        overlay = OVERLAY_DIR / f"{run_id}.qcow2"
        _run([
            "qemu-img", "create", "-f", "qcow2",
            "-b", str(Path(base_rootfs).resolve()),
            "-F", "qcow2",
            str(overlay),
        ])
        return str(overlay)

    @staticmethod
    def _build_qemu_cmd(fw: dict, tap: str, mac: str,
                        overlay_path: str | None = None,
                        tap_int: str | None = None, mac_int: str | None = None) -> list[str]:
        fw_dir = Path(fw["_dir"])
        kernel = str(fw_dir / fw["kernel"])
        rootfs = str(fw_dir / fw["rootfs"]) if fw.get("rootfs") else None
        arch = fw["arch"]
        machine = fw.get("qemu_machine", "malta")
        mem = fw.get("memory", "256")

        # Use the overlay instead of the base image if provided
        drive_file = overlay_path or rootfs

        # Architecture-specific defaults
        ARCH_PROFILES = {
            "mipsel": {
                "qemu_bin": "qemu-system-mipsel",
                "append": "root=/dev/sda1 console=ttyS0",
                "drive": ["-drive", f"file={drive_file},format=qcow2"],
                "supports_multi": True,
            },
            "armel": {
                "qemu_bin": "qemu-system-arm",
                "append": "root=/dev/sda1 console=ttyAMA0",
                "drive": ["-drive", f"file={drive_file},format=qcow2"],
                "supports_multi": True,
            },
            "cortex-m3": {
                "qemu_bin": "qemu-system-arm",
                "drive": [],
                # The lm3s6965evb SoC has a built-in Stellaris Ethernet
                # controller whose MAC is fixed at 00:00:94:00:83:00.
                # -net nic,model=stellaris wires it to the hub; macaddr
                # has no effect on the SoC MAC.
                "net": [
                    "-net", "nic,model=stellaris",
                    "-net", f"tap,ifname={tap},script=no,downscript=no",
                ],
            },
            "riscv32": {
                "qemu_bin": "qemu-system-riscv32",
                "drive": [],
                # RISC-V virt board — virtio-net-device (requires future
                # Zephyr virtio-net driver; placeholder for SLIP bridge).
                "net": [
                    "-netdev", f"tap,id=net0,ifname={tap},script=no,downscript=no",
                    "-device", f"virtio-net-device,netdev=net0,mac={mac}",
                ],
            },
        }

        profile = ARCH_PROFILES.get(arch)
        if profile is None:
            raise ValueError(f"Unsupported arch: {arch}")

        # Build network configuration (single or multi-homed)
        if tap_int and mac_int and profile.get("supports_multi"):
            # Multi-homed: two network interfaces
            if arch == "mipsel":
                net_config = [
                    "-netdev", f"tap,id=net0,ifname={tap},script=no,downscript=no",
                    "-device", f"e1000,netdev=net0,mac={mac}",
                    "-netdev", f"tap,id=net1,ifname={tap_int},script=no,downscript=no",
                    "-device", f"e1000,netdev=net1,mac={mac_int}",
                ]
            elif arch == "armel":
                net_config = [
                    "-net", f"nic,macaddr={mac}",
                    "-net", f"tap,ifname={tap},script=no,downscript=no",
                    "-net", f"nic,macaddr={mac_int}",
                    "-net", f"tap,ifname={tap_int},script=no,downscript=no",
                ]
            else:
                raise ValueError(f"Multi-homed not supported for arch: {arch}")
        else:
            # Single interface
            if arch == "mipsel":
                net_config = [
                    "-netdev", f"tap,id=net0,ifname={tap},script=no,downscript=no",
                    "-device", f"e1000,netdev=net0,mac={mac}",
                ]
            elif arch == "armel":
                net_config = [
                    "-net", f"nic,macaddr={mac}",
                    "-net", f"tap,ifname={tap},script=no,downscript=no",
                ]
            else:
                # cortex-m3 and riscv32 handled separately below
                net_config = []

        # MCU / bare-metal targets (no rootfs, no -append)
        if arch == "cortex-m3":
            # cortex-m3 doesn't support multi-homed (single Stellaris MAC)
            net_config = [
                "-net", "nic,model=stellaris",
                "-net", f"tap,ifname={tap},script=no,downscript=no",
            ]
            cmd = [
                profile["qemu_bin"],
                "-M", machine,
                "-kernel", kernel,
                "-nographic",
                *net_config,
            ]
            return cmd

        if arch == "riscv32":
            # riscv32 doesn't support multi-homed yet
            net_config = [
                "-netdev", f"tap,id=net0,ifname={tap},script=no,downscript=no",
                "-device", f"virtio-net-device,netdev=net0,mac={mac}",
            ]
            cmd = [
                profile["qemu_bin"],
                "-M", machine,
                "-bios", "none",
                "-m", "256",
                "-kernel", kernel,
                "-nographic",
                *net_config,
            ]
            return cmd

        cmd = [
            profile["qemu_bin"],
            "-M", machine,
            "-kernel", kernel,
            *profile["drive"],
            "-nographic",
            "-append", profile["append"],
            "-m", str(mem),
            *net_config,
        ]

        # Optional initrd (required by some compressed kernels)
        if "initrd" in fw:
            initrd = str(fw_dir / fw["initrd"])
            cmd.extend(["-initrd", initrd])

        return cmd

    # -- lifecycle -----------------------------------------------------------

    # Stellaris lm3s6965evb SoC hardcodes this MAC in its Ethernet controller
    STELLARIS_MAC = "00:00:94:00:83:00"

    def spawn_instance(self, firmware_id: str) -> str:
        """Boot a new QEMU instance. Returns a unique run_id."""
        fw = self._load_firmware(firmware_id)
        multi_homed = fw.get("multi_homed", False)

        # Stellaris lm3s6965evb shares a single hardcoded MAC across all
        # cortex-m3 instances — only one may be on the bridge at a time.
        if fw["arch"] == "cortex-m3":
            for inst in self.active_instances.values():
                if inst["arch"] == "cortex-m3" and inst["_proc"].poll() is None:
                    raise RuntimeError(
                        f"Only one cortex-m3 device allowed at a time "
                        f"(Stellaris MAC conflict). Running: {inst['id']}"
                    )

        tap = self._get_next_tap()
        mac = self.STELLARIS_MAC if fw["arch"] == "cortex-m3" else self._generate_mac()
        
        # Multi-homed setup: create second TAP and MAC
        tap_int = None
        mac_int = None
        if multi_homed:
            # Ensure internal bridge exists
            LabManager._ensure_internal_bridge()
            tap_int = self._get_next_tap("_int")
            mac_int = self._generate_mac()
            self._create_tap(tap_int, BRIDGE_INTERNAL)
            log.info("Multi-homed device: external=%s, internal=%s", tap, tap_int)
        
        run_id = f"{firmware_id}_{uuid.uuid4().hex[:8]}"

        # Validate files exist before touching the network
        fw_dir = Path(fw["_dir"])
        if not (fw_dir / fw["kernel"]).is_file():
            raise FileNotFoundError(f"Kernel missing: {fw_dir / fw['kernel']}")
        if fw.get("rootfs") and not (fw_dir / fw["rootfs"]).is_file():
            raise FileNotFoundError(f"Rootfs missing: {fw_dir / fw['rootfs']}")

        self._create_tap(tap)

        # Create a per-instance qcow2 overlay for Linux firmware so
        # multiple VMs can share the same base image concurrently.
        overlay_path = None
        if fw.get("rootfs"):
            overlay_path = self._create_overlay(
                str(fw_dir / fw["rootfs"]), run_id)

        cmd = self._build_qemu_cmd(fw, tap, mac, overlay_path, tap_int, mac_int)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"qemu-{run_id}.log"
        log_file = open(log_path, "w")

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            self._destroy_tap(tap)
            if tap_int:
                self._destroy_tap(tap_int)
            raise

        self.active_instances[run_id] = {
            "id": run_id,
            "firmware_id": firmware_id,
            "arch": fw["arch"],
            "name": fw.get("name", firmware_id),
            "pid": proc.pid,
            "tap": tap,
            "mac": mac,
            "ip": "pending",
            "ip_internal": "pending" if multi_homed else None,
            "tap_internal": tap_int,
            "mac_internal": mac_int,
            "multi_homed": multi_homed,
            "log": str(log_path),
            "_proc": proc,
            "_log_fh": log_file,
            "_overlay": overlay_path,
        }
        log_msg = f"Spawned {run_id}  PID={proc.pid}  TAP={tap}  MAC={mac}"
        if multi_homed:
            log_msg += f"  TAP_INT={tap_int}  MAC_INT={mac_int}"
        log.info(log_msg)
        return run_id

    def stop_instance(self, run_id: str) -> bool:
        """Stop a specific instance and clean up."""
        inst = self.active_instances.pop(run_id, None)
        if inst is None:
            return False

        proc: subprocess.Popen = inst["_proc"]
        if proc.poll() is None:
            log.info("Stopping %s (PID %d)...", run_id, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        inst["_log_fh"].close()
        self._destroy_tap(inst["tap"])
        
        # Clean up internal TAP if multi-homed
        if inst.get("tap_internal"):
            self._destroy_tap(inst["tap_internal"])

        # Remove the per-instance qcow2 overlay
        if inst.get("_overlay"):
            try:
                Path(inst["_overlay"]).unlink(missing_ok=True)
            except OSError:
                pass

        log.info("Cleaned up %s", run_id)
        return True

    def reset_lab(self) -> int:
        """Kill every running instance. Returns count stopped."""
        ids = list(self.active_instances.keys())
        for rid in ids:
            self.stop_instance(rid)
        return len(ids)

    def get_topology(self) -> list[dict]:
        """Return sanitised list of active instances (no internal objects)."""
        topo = []
        for inst in self.active_instances.values():
            alive = inst["_proc"].poll() is None
            entry = {
                "id": inst["id"],
                "firmware_id": inst["firmware_id"],
                "arch": inst["arch"],
                "name": inst["name"],
                "pid": inst["pid"],
                "tap": inst["tap"],
                "mac": inst["mac"],
                "ip": inst["ip"],
                "alive": alive,
            }
            # Add multi-homed fields if applicable
            if inst.get("multi_homed"):
                entry["ip_internal"] = inst.get("ip_internal", "pending")
                entry["tap_internal"] = inst.get("tap_internal")
                entry["mac_internal"] = inst.get("mac_internal")
            topo.append(entry)
        return topo

    def refresh_ips(self) -> None:
        """Scan dnsmasq leases and update guest IPs (both external and internal)."""
        # Refresh external IPs from br0
        if LEASE_FILE.exists():
            leases = LEASE_FILE.read_text().splitlines()
            for inst in self.active_instances.values():
                if inst["ip"] not in ("pending", "unknown"):
                    continue
                for line in leases:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1].lower() == inst["mac"].lower():
                        inst["ip"] = parts[2]
                        log.info("%s acquired external IP %s", inst["id"], parts[2])
                        break
        
        # Refresh internal IPs from br_internal for multi-homed devices
        if LEASE_FILE_INTERNAL.exists():
            leases_int = LEASE_FILE_INTERNAL.read_text().splitlines()
            for inst in self.active_instances.values():
                if not inst.get("multi_homed") or not inst.get("mac_internal"):
                    continue
                if inst.get("ip_internal") not in ("pending", "unknown", None):
                    continue
                for line in leases_int:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1].lower() == inst["mac_internal"].lower():
                        inst["ip_internal"] = parts[2]
                        log.info("%s acquired internal IP %s", inst["id"], parts[2])
                        break

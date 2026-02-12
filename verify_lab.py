#!/usr/bin/env python3
"""verify_lab.py — Self-test for IoT Lab Phase 1 infrastructure.

Checks:
  1. Bridge br0 is UP and has 192.168.100.1
  2. dnsmasq is running on br0
  3. QEMU binaries are executable
  4. Gateway 192.168.100.1 responds to ping
  5. Firmware files are present
"""

import shutil
import subprocess
import sys

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
WARN = "\033[93m[WARN]\033[0m"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag} {name}{suffix}")
    results.append((name, ok))
    return ok


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> None:
    print("\n===== IoT Lab Phase 1 Verification =====\n")

    # 1. Bridge br0 exists and has correct IP
    r = run(["ip", "addr", "show", "br0"])
    br0_up = r.returncode == 0 and (",UP" in r.stdout or "state UP" in r.stdout)
    check("br0 interface exists and is UP", br0_up,
          "found" if br0_up else r.stderr.strip())

    br0_ip = "192.168.100.1" in (r.stdout if r.returncode == 0 else "")
    check("br0 has IP 192.168.100.1/24", br0_ip)

    # 2. dnsmasq running on br0
    r = run(["pgrep", "-a", "dnsmasq"])
    dnsmasq_ok = r.returncode == 0 and "br0" in r.stdout
    check("dnsmasq running on br0", dnsmasq_ok,
          r.stdout.strip().split("\n")[0] if dnsmasq_ok else "not found")

    # 3. QEMU binaries
    for binary in ["qemu-system-mipsel", "qemu-system-arm"]:
        path = shutil.which(binary)
        check(f"{binary} is executable", path is not None,
              path or "not in PATH")

    # 4. Ping gateway
    r = run(["ping", "-c", "2", "-W", "2", "192.168.100.1"])
    ping_ok = r.returncode == 0
    check("Ping gateway 192.168.100.1", ping_ok,
          "reachable" if ping_ok else "unreachable")

    # 5. Firmware files
    from pathlib import Path
    fw_dir = Path.home() / "iot-lab" / "library" / "dvrf_v03"
    kernel = fw_dir / "vmlinux-3.2.0-4-4kc-malta"
    rootfs = fw_dir / "rootfs.img"
    check("Kernel image present", kernel.is_file(), str(kernel))
    check("Rootfs image present", rootfs.is_file(), str(rootfs))

    # 6. IP forwarding
    r = run(["sysctl", "-n", "net.ipv4.ip_forward"])
    fwd_ok = r.stdout.strip() == "1"
    check("IP forwarding enabled", fwd_ok, f"value={r.stdout.strip()}")

    # Summary
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"\n{'='*42}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*42}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

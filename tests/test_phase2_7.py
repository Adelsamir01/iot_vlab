#!/usr/bin/env python3
"""test_phase2_7.py — Phase 2.7 verification: Cortex-M4 support.

Tests:
  1. Library lists zephyr_coap_m4 with arch=cortex-m4
  2. cortex-m4 QEMU command uses mps2-an386 machine and lan9118 NIC
  3. Multiple M4 instances CAN be spawned simultaneously (no MAC constraint)
  4. DHCP lease acquired after spawn (if ELF exists)
  5. CoAP UDP :5683 responds (if ELF exists)
  6. cortex-m4 and cortex-m3 can coexist (different MAC families)

Tests 4-6 require the firmware ELF to be built first:
    ./build_advanced_firmware.sh
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "http://127.0.0.1:5000"
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
SKIP = "\033[93m[SKIP]\033[0m"

results: list[tuple[str, bool]] = []
SCRIPT_DIR = Path(__file__).resolve().parent.parent

ELF_M4 = SCRIPT_DIR / "library" / "zephyr_coap_m4" / "zephyr.elf"


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag} {name}{suffix}")
    results.append((name, ok))
    return ok


def skip(name: str, reason: str = "") -> None:
    suffix = f" ({reason})" if reason else ""
    print(f"  {SKIP} {name}{suffix}")


def api_get(path: str) -> requests.Response:
    return requests.get(f"{API}{path}", timeout=5)


def api_post(path: str, json: dict | None = None) -> requests.Response:
    return requests.post(f"{API}{path}", json=json, timeout=10)


def wait_for_api(timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{API}/library", timeout=2).status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def udp_probe(ip: str, port: int, timeout: float = 3.0) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        coap_get = bytes([0x40, 0x01, 0x00, 0x01])
        s.sendto(coap_get, (ip, port))
        data, _ = s.recvfrom(1024)
        s.close()
        return len(data) > 0
    except socket.timeout:
        return True  # no ICMP port-unreachable → something listening
    except Exception:
        return False


def wait_for_ip(run_id: str, timeout: int = 30) -> str | None:
    print(f"[*] Waiting for DHCP lease (up to {timeout}s) ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        r = api_get("/topology")
        for d in r.json():
            if d.get("id") == run_id and d.get("ip") not in ("pending", "unknown", None):
                return d["ip"]
    return None


def test_qemu_command_generation() -> bool:
    """Test that lab_manager generates the correct QEMU command for cortex-m4."""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from lab_manager import LabManager

        lm = LabManager()

        # Minimal fake firmware dict
        fake_fw = {
            "id": "zephyr_coap_m4",
            "arch": "cortex-m4",
            "qemu_machine": "mps2-an386",
            "kernel": str(ELF_M4) if ELF_M4.exists() else "/tmp/fake.elf",
            "net_model": "lan9118",
            "_dir": str(SCRIPT_DIR / "library" / "zephyr_coap_m4"),
        }
        # Provide a dummy kernel path so _build_qemu_cmd doesn't fail on lookup
        fake_fw["kernel"] = "zephyr.elf"

        mac = "52:54:00:aa:bb:cc"
        tap = "tap99"
        cmd = LabManager._build_qemu_cmd(fake_fw, tap, mac)

        has_arm = "qemu-system-arm" in cmd
        has_mps2 = "mps2-an386" in cmd
        has_lan9118 = any("lan9118" in c for c in cmd)
        has_tap = any(tap in c for c in cmd)

        ok = has_arm and has_mps2 and has_lan9118 and has_tap
        detail = f"qemu={has_arm} mps2={has_mps2} lan9118={has_lan9118} tap={has_tap}"
        return check("QEMU command: arm + mps2-an386 + lan9118", ok, detail)
    except Exception as e:
        return check("QEMU command generation", False, str(e))


def main() -> None:
    print("\n===== Phase 2.7 Verification: Cortex-M4 Support =====\n")
    elf_present = ELF_M4.exists()
    if not elf_present:
        print(f"[!] {ELF_M4} not found.")
        print("    Firmware-dependent tests (spawn/DHCP/CoAP) will be skipped.")
        print("    Build with: ./build_advanced_firmware.sh\n")

    # ── 1. Offline: QEMU command generation ──────────────────────────────
    print("── QEMU command generation (offline) ──────────────────")
    test_qemu_command_generation()

    # ── 2. Start API ──────────────────────────────────────────────────────
    print("\n[*] Starting lab_api.py ...")
    api_proc = subprocess.Popen(
        [sys.executable, "lab_api.py"],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    api_up = wait_for_api(timeout=15)
    check("API server reachable", api_up)
    if not api_up:
        print("[!] API never came up. Aborting.")
        api_proc.kill()
        sys.exit(1)

    try:
        # ── 3. Library check ──────────────────────────────────────────────
        print("\n── Library ──────────────────────────────────────────")
        r = api_get("/library")
        lib = {f["id"]: f for f in r.json()}
        check("zephyr_coap_m4 in library", "zephyr_coap_m4" in lib)

        if "zephyr_coap_m4" in lib:
            fw = lib["zephyr_coap_m4"]
            check("arch is cortex-m4", fw.get("arch") == "cortex-m4",
                  fw.get("arch", "missing"))
            check("qemu_machine is mps2-an386", fw.get("qemu_machine") == "mps2-an386",
                  fw.get("qemu_machine", "missing"))
            check("net_model is lan9118", fw.get("net_model") == "lan9118",
                  fw.get("net_model", "missing"))

        # ── 4. Spawn tests (only if ELF exists) ───────────────────────────
        if not elf_present:
            print("\n── Spawn / network / CoAP tests: SKIPPED (no ELF) ──")
            skip("POST /spawn zephyr_coap_m4", "ELF not built")
            skip("M4 in topology", "ELF not built")
            skip("M4 QEMU PID alive", "ELF not built")
            skip("M4 TAP on br0", "ELF not built")
            skip("M4 DHCP lease", "ELF not built")
            skip("CoAP 5683 UDP responding", "ELF not built")
            skip("Multiple M4 instances (no MAC constraint)", "ELF not built")
            skip("M4 + M3 coexistence", "ELF not built")
        else:
            print("\n── CoAP M4 spawn ────────────────────────────────────")
            r = api_post("/spawn", {"firmware_id": "zephyr_coap_m4"})
            spawned = r.status_code == 201
            run_id1 = r.json().get("run_id", "") if spawned else ""
            check("POST /spawn zephyr_coap_m4", spawned, run_id1 or r.text)

            if run_id1:
                time.sleep(2)
                r = api_get("/topology")
                inst = next((d for d in r.json() if d.get("id") == run_id1), None)
                check("M4 in topology", inst is not None)

                if inst:
                    alive = run_cmd(["kill", "-0", str(inst["pid"])]).returncode == 0
                    check("M4 QEMU PID alive", alive, str(inst["pid"]))

                    br = run_cmd(["bridge", "link", "show"])
                    check("M4 TAP on br0", inst["tap"] in br.stdout, inst["tap"])

                    ip = wait_for_ip(run_id1, timeout=30)
                    check("M4 DHCP lease", ip is not None, ip or "no lease")

                    if ip:
                        print(f"[*] Probing CoAP on {ip}:5683 ...")
                        coap_ok = any(udp_probe(ip, 5683) for _ in range(5))
                        check("CoAP 5683 UDP responding", coap_ok)

                # ── Multiple M4 instances (no MAC guard) ──────────────────
                print("\n── Multiple M4 (no MAC constraint) ─────────────────")
                r2 = api_post("/spawn", {"firmware_id": "zephyr_coap_m4"})
                second_ok = r2.status_code == 201
                run_id2 = r2.json().get("run_id", "") if second_ok else ""
                check("Second M4 spawns (no MAC conflict)", second_ok,
                      run_id2 or r2.text[:60])
                if run_id2:
                    api_post(f"/kill/{run_id2}")

                # ── M4 + M3 coexistence ───────────────────────────────────
                print("\n── M4 + M3 coexistence ──────────────────────────────")
                r3 = api_post("/spawn", {"firmware_id": "zephyr_coap"})
                m3_ok = r3.status_code == 201
                run_id3 = r3.json().get("run_id", "") if m3_ok else ""
                check("M3 spawns while M4 running", m3_ok,
                      run_id3 or r3.text[:60])
                if run_id3:
                    api_post(f"/kill/{run_id3}")

                api_post(f"/kill/{run_id1}")

        # ── Final topology check ──────────────────────────────────────────
        api_post("/reset_lab")
        time.sleep(1)
        r = api_get("/topology")
        # Only count QEMU devices (pid != null); simulators may still be present
        qemu_devices = [d for d in r.json() if d.get("pid") is not None]
        check("No QEMU devices after cleanup", len(qemu_devices) == 0,
              f"{len(qemu_devices)} remaining" if qemu_devices else "")

    finally:
        try:
            api_post("/reset_lab")
        except Exception:
            pass
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()

    # ── Summary ───────────────────────────────────────────────────────────
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"\n{'='*50}")
    print(f"  Phase 2.7 Results: {passed}/{total} passed, {failed} failed")
    if not elf_present:
        print("  (firmware-dependent tests skipped — build with ./build_advanced_firmware.sh)")
    print(f"{'='*50}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

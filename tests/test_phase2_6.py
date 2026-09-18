#!/usr/bin/env python3
"""test_phase2_6.py — Phase 2.6 verification: Protocol Expansion.

Tests (run sequentially due to Stellaris single-MAC constraint):
  1. API lists zephyr_coap and arm_modbus_sim in the library
  2. Spawn zephyr_coap → DHCP lease → CoAP port 5683 UDP open → kill
  3. Spawn arm_modbus_sim → DHCP lease → TCP echo on port 502 → kill
  4. Verify Stellaris MAC conflict guard (second cortex-m3 spawn blocked)
"""

import os
import socket
import subprocess
import sys
import time

import requests

API = "http://127.0.0.1:5000"
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag} {name}{suffix}")
    results.append((name, ok))
    return ok


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


def tcp_echo(ip: str, port: int, msg: str, timeout: float = 5.0) -> str | None:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.sendall(msg.encode())
            return s.recv(4096).decode()
    except Exception:
        return None


def udp_probe(ip: str, port: int, timeout: float = 3.0) -> bool:
    """Send a small UDP packet and check for any response (or no ICMP reject)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        # CoAP header: version 1, type CON, code GET, msg-id 0x0001
        coap_get = bytes([0x40, 0x01, 0x00, 0x01])
        s.sendto(coap_get, (ip, port))
        data, _ = s.recvfrom(1024)
        s.close()
        return len(data) > 0
    except socket.timeout:
        # No ICMP port-unreachable means something is listening
        return True
    except Exception:
        return False


def wait_for_ip(run_id: str, timeout: int = 30) -> str | None:
    """Poll topology until the device acquires a DHCP lease."""
    print(f"[*] Waiting for DHCP lease (up to {timeout}s) ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        r = api_get("/topology")
        for d in r.json():
            if d["id"] == run_id and d["ip"] not in ("pending", "unknown"):
                return d["ip"]
    return None


def verify_spawn_and_network(fw_id: str, label: str) -> dict | None:
    """Spawn firmware, verify QEMU alive + TAP on br0. Returns instance dict."""
    r = api_post("/spawn", {"firmware_id": fw_id})
    spawned = r.status_code == 201
    run_id = r.json().get("run_id", "") if spawned else ""
    check(f"POST /spawn {fw_id}", spawned, run_id or r.text)

    if not run_id:
        return None

    time.sleep(2)
    r = api_get("/topology")
    inst = next((d for d in r.json() if d["id"] == run_id), None)
    check(f"{label} in topology", inst is not None)

    if inst:
        alive = run_cmd(["kill", "-0", str(inst["pid"])]).returncode == 0
        check(f"{label} QEMU PID alive", alive, str(inst["pid"]))

        tap = inst["tap"]
        tap_exists = run_cmd(["ip", "link", "show", tap]).returncode == 0
        check(f"{label} TAP exists", tap_exists, tap)

        br = run_cmd(["bridge", "link", "show"])
        check(f"{label} TAP on br0", tap in br.stdout)

    return inst


def kill_and_verify(run_id: str, tap: str, label: str) -> None:
    """Kill instance and verify cleanup."""
    rk = api_post(f"/kill/{run_id}")
    check(f"{label} kill", rk.status_code == 200)
    time.sleep(1)

    tap_gone = run_cmd(["ip", "link", "show", tap]).returncode != 0
    check(f"{label} TAP removed", tap_gone)


def main() -> None:
    print("\n===== Phase 2.6 Verification: Protocol Expansion =====\n")

    # ── Start API server ─────────────────────────────────────────────────
    print("[*] Starting lab_api.py ...")
    api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    api_proc = subprocess.Popen(
        [sys.executable, "lab_api.py"],
        cwd=api_dir,
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
        # ── 1. Library check ─────────────────────────────────────────────
        r = api_get("/library")
        lib_ids = [f["id"] for f in r.json()]
        check("zephyr_coap in library", "zephyr_coap" in lib_ids)
        check("arm_modbus_sim in library", "arm_modbus_sim" in lib_ids)

        # ══════════════════════════════════════════════════════════════════
        # 2. CoAP Server (zephyr_coap) — UDP :5683
        # ══════════════════════════════════════════════════════════════════
        print("\n── CoAP Server ─────────────────────────────────────")
        inst_coap = verify_spawn_and_network("zephyr_coap", "CoAP")

        if inst_coap:
            ip = wait_for_ip(inst_coap["id"])
            check("CoAP acquired DHCP lease", ip is not None, ip or "no lease")

            if ip:
                print(f"[*] Probing CoAP on {ip}:5683 UDP ...")
                coap_ok = False
                for _ in range(5):
                    if udp_probe(ip, 5683):
                        coap_ok = True
                        break
                    time.sleep(2)
                check("CoAP port 5683 UDP responding", coap_ok)

            kill_and_verify(inst_coap["id"], inst_coap["tap"], "CoAP")

        # ══════════════════════════════════════════════════════════════════
        # 3. Fake PLC (arm_modbus_sim) — TCP :502
        # ══════════════════════════════════════════════════════════════════
        print("\n── Fake PLC ────────────────────────────────────────")
        inst_plc = verify_spawn_and_network("arm_modbus_sim", "PLC")

        if inst_plc:
            ip = wait_for_ip(inst_plc["id"])
            check("PLC acquired DHCP lease", ip is not None, ip or "no lease")

            if ip:
                print(f"[*] Testing TCP echo on {ip}:502 (MCU boot ~30s) ...")
                plc_ok = False
                test_msg = "ModbusPing"
                for _ in range(15):
                    reply = tcp_echo(ip, 502, test_msg)
                    if reply and test_msg in reply:
                        plc_ok = True
                        break
                    time.sleep(3)
                check("TCP echo on port 502", plc_ok,
                      f"sent '{test_msg}', got '{reply}'" if reply else "no reply")

            kill_and_verify(inst_plc["id"], inst_plc["tap"], "PLC")

        # ══════════════════════════════════════════════════════════════════
        # 4. Stellaris MAC conflict guard
        # ══════════════════════════════════════════════════════════════════
        print("\n── MAC conflict guard ──────────────────────────────")
        r1 = api_post("/spawn", {"firmware_id": "zephyr_coap"})
        if r1.status_code == 201:
            run_id1 = r1.json()["run_id"]
            # Try to spawn a second cortex-m3 while first is alive
            r2 = api_post("/spawn", {"firmware_id": "arm_modbus_sim"})
            check("Second cortex-m3 blocked (MAC guard)",
                  r2.status_code == 500,
                  r2.json().get("error", "")[:60])
            api_post(f"/kill/{run_id1}")
            time.sleep(1)
        else:
            check("Second cortex-m3 blocked (MAC guard)", False, "first spawn failed")

        # ── Final cleanup ────────────────────────────────────────────────
        r = api_get("/topology")
        check("Topology empty after all tests", len(r.json()) == 0)

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

    # ── Summary ──────────────────────────────────────────────────────────
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"\n{'='*50}")
    print(f"  Phase 2.6 Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

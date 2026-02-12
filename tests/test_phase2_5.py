#!/usr/bin/env python3
"""test_phase2_5.py — Phase 2.5 verification: Zephyr Cortex-M3 on br0.

Tests:
  1. API spawns zephyr_echo firmware
  2. QEMU process is alive
  3. TAP interface exists and is attached to br0
  4. Device acquires DHCP lease (IP from dnsmasq)
  5. TCP echo on port 4242 works
  6. Clean kill and TAP removal
"""

import os
import signal
import socket
import subprocess
import sys
import time

import requests

API = "http://127.0.0.1:5000"
FIRMWARE_ID = "zephyr_echo"
ECHO_PORT = 4242
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
    """Send msg over TCP and return the response (or None on failure)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.sendall(msg.encode())
            return s.recv(4096).decode()
    except Exception:
        return None


def main() -> None:
    print("\n===== Phase 2.5 Verification: Industrial IoT (Cortex-M3) =====\n")

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

    run_id = ""
    try:
        # ── 1. Verify zephyr_echo in library ─────────────────────────────
        r = api_get("/library")
        lib = r.json()
        check("zephyr_echo in firmware library",
              any(f["id"] == FIRMWARE_ID for f in lib))

        # ── 2. Spawn ─────────────────────────────────────────────────────
        r = api_post("/spawn", {"firmware_id": FIRMWARE_ID})
        spawned = r.status_code == 201
        run_id = r.json().get("run_id", "") if spawned else ""
        check("POST /spawn zephyr_echo", spawned, run_id)

        if not run_id:
            print("[!] Spawn failed, skipping remaining tests.")
            return

        # ── 3. QEMU process alive ────────────────────────────────────────
        time.sleep(2)
        r = api_get("/topology")
        topo = r.json()
        inst = next((d for d in topo if d["id"] == run_id), None)
        check("Instance in topology", inst is not None)

        if inst:
            alive = run_cmd(["kill", "-0", str(inst["pid"])]).returncode == 0
            check("QEMU PID alive", alive, str(inst["pid"]))

            # ── 4. TAP interface on br0 ──────────────────────────────────
            tap = inst["tap"]
            tap_exists = run_cmd(["ip", "link", "show", tap]).returncode == 0
            check("TAP interface exists", tap_exists, tap)

            br = run_cmd(["bridge", "link", "show"])
            tap_on_br0 = tap in br.stdout
            check("TAP attached to br0", tap_on_br0)

            # ── 5. Wait for DHCP lease ───────────────────────────────────
            # MCU boots fast; poll for IP over ~30s
            ip_addr = None
            print("[*] Waiting for DHCP lease (up to 30s) ...")
            for attempt in range(15):
                time.sleep(2)
                r = api_get("/topology")
                topo = r.json()
                inst = next((d for d in topo if d["id"] == run_id), None)
                if inst and inst["ip"] not in ("pending", "unknown"):
                    ip_addr = inst["ip"]
                    break
            check("Device acquired DHCP lease", ip_addr is not None,
                  ip_addr or "no lease")

            # ── 6. TCP echo test ─────────────────────────────────────────
            if ip_addr:
                echo_ok = False
                test_msg = "Hello Industrial"
                print(f"[*] Testing TCP echo on {ip_addr}:{ECHO_PORT} ...")
                # Give the echo server a moment after boot
                for attempt in range(5):
                    reply = tcp_echo(ip_addr, ECHO_PORT, test_msg)
                    if reply and test_msg in reply:
                        echo_ok = True
                        break
                    time.sleep(2)
                check("TCP echo on port 4242", echo_ok,
                      f"sent '{test_msg}', got '{reply}'" if reply else "no reply")

        # ── 7. Kill and verify cleanup ───────────────────────────────────
        if run_id:
            rk = api_post(f"/kill/{run_id}")
            check("POST /kill instance", rk.status_code == 200)
            time.sleep(1)

            if inst:
                tap_gone = run_cmd(["ip", "link", "show", inst["tap"]]).returncode != 0
                check("TAP removed after kill", tap_gone)

            r = api_get("/topology")
            check("Topology empty after kill", len(r.json()) == 0)

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
    print(f"  Phase 2.5 Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

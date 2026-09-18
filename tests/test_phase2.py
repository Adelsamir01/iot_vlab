#!/usr/bin/env python3
"""test_phase2.py — Integration test for the IoT Lab orchestration layer.

Starts the Flask API, spawns two DVRF instances, verifies networking, then cleans up.
"""

import os
import signal
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
    """Poll until the API responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{API}/library", timeout=2)
            if r.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> None:
    print("\n===== Phase 2 Integration Test =====\n")

    # ── 1. Start the API server ──────────────────────────────────────────
    print("[*] Starting lab_api.py ...")
    api_dir = str(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    api_proc = subprocess.Popen(
        [sys.executable, "lab_api.py"],
        cwd=api_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    api_up = wait_for_api(timeout=15)
    check("API server is reachable", api_up)
    if not api_up:
        print("[!] API never came up. Aborting.")
        api_proc.kill()
        sys.exit(1)

    run_ids = []

    try:
        # ── 2. GET /library ──────────────────────────────────────────────
        r = api_get("/library")
        lib = r.json()
        check("GET /library returns firmware list", r.status_code == 200 and len(lib) > 0,
              f"{len(lib)} firmware(s)")
        check("dvrf_v03 in library", any(f["id"] == "dvrf_v03" for f in lib))

        # ── 3. Spawn two instances ───────────────────────────────────────
        r1 = api_post("/spawn", {"firmware_id": "dvrf_v03"})
        ok1 = r1.status_code == 201
        rid1 = r1.json().get("run_id", "") if ok1 else ""
        check("POST /spawn #1", ok1, rid1)
        if rid1:
            run_ids.append(rid1)

        r2 = api_post("/spawn", {"firmware_id": "dvrf_v03"})
        ok2 = r2.status_code == 201
        rid2 = r2.json().get("run_id", "") if ok2 else ""
        check("POST /spawn #2", ok2, rid2)
        if rid2:
            run_ids.append(rid2)

        # ── 4. Verify topology ───────────────────────────────────────────
        r = api_get("/topology")
        topo = r.json()
        check("GET /topology returns 2 instances", len(topo) == 2,
              f"got {len(topo)}")

        # ── 5. Verify TAP interfaces on host ─────────────────────────────
        taps_expected = [inst["tap"] for inst in topo]
        taps_ok = True
        for tap in taps_expected:
            rc = run_cmd(["ip", "link", "show", tap])
            if rc.returncode != 0:
                taps_ok = False
        check("TAP interfaces exist on host", taps_ok, ", ".join(taps_expected))

        # Verify TAPs are attached to br0
        br = run_cmd(["bridge", "link", "show"])
        bridge_ok = all(tap in br.stdout for tap in taps_expected)
        check("TAPs attached to br0", bridge_ok)

        # ── 6. Verify PIDs are alive ─────────────────────────────────────
        pids_alive = all(
            run_cmd(["kill", "-0", str(inst["pid"])]).returncode == 0
            for inst in topo
        )
        check("QEMU PIDs are alive", pids_alive,
              ", ".join(str(i["pid"]) for i in topo))

        # ── 7. Spawn with bad firmware_id → 404 ─────────────────────────
        r_bad = api_post("/spawn", {"firmware_id": "nonexistent"})
        check("Spawn bad firmware returns 404", r_bad.status_code == 404)

        # ── 8. Kill instance #1 ─────────────────────────────────────────
        if rid1:
            tap1 = topo[0]["tap"] if topo else "tap0"
            rk = api_post(f"/kill/{rid1}")
            check("POST /kill instance #1", rk.status_code == 200,
                  rk.json().get("status", ""))

            # Verify TAP removed
            time.sleep(1)
            rc = run_cmd(["ip", "link", "show", tap1])
            check("TAP removed after kill", rc.returncode != 0, tap1)

        # ── 9. Topology now has 1 instance ──────────────────────────────
        r = api_get("/topology")
        topo2 = r.json()
        check("Topology shows 1 instance after kill", len(topo2) == 1,
              f"got {len(topo2)}")

        # ── 10. Reset lab ───────────────────────────────────────────────
        rr = api_post("/reset_lab")
        check("POST /reset_lab", rr.status_code == 200,
              f"stopped {rr.json().get('stopped', '?')}")

        r = api_get("/topology")
        check("Topology empty after reset", len(r.json()) == 0)

    finally:
        # ── Cleanup: kill any leftover instances via API then stop server ─
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
    print(f"\n{'='*42}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*42}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

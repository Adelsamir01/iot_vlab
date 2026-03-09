#!/usr/bin/env python3
"""test_agent_view.py — Verify APIOT dashboard integration.

Tests the build_agent_view() aggregation and /api/agent_state endpoint
using temporary fixture files. No sudo or running QEMU required.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag} {name}{suffix}")
    results.append((name, ok))
    return ok


NETWORK_STATE = {
    "discovered_hosts": {
        "192.168.100.35": {"mac": "00:00:94:00:83:00", "vendor": "Asante Technologies"}
    },
    "fingerprints": {
        "192.168.100.35": {
            "ip": "192.168.100.35",
            "ports": {
                "4242": {"state": "open", "protocol": "tcp", "service": "echo", "version": ""}
            },
            "os_guess": None
        }
    },
    "active_vulnerabilities": {
        "abc123": {
            "ip": "192.168.100.35",
            "attack": "crash_verified",
            "verification": {"status": "crashed", "verified": True, "details": "ICMP ping failed"},
            "timestamp": 1772728759.78
        }
    }
}

ATTACK_LOG = [
    {
        "timestamp": 1772728607.39,
        "target_ip": "192.168.100.35",
        "tool_used": "brute_force_telnet",
        "outcome": "delivered",
        "packets_sent": 3
    },
    {
        "timestamp": 1772728759.78,
        "target_ip": "192.168.100.35",
        "tool_used": "verify_crash",
        "outcome": "crash_verified",
        "packets_sent": 2
    }
]

REMEDIATION_LOG = [
    {
        "timestamp": 1772725346.28,
        "attack": "coap_option_overflow",
        "target_ip": "192.168.100.35",
        "rule": "iptables -A FORWARD -p udp --dport 5683 -m length --length 0:7 -j DROP",
        "applied": True
    }
]


def main() -> None:
    print("\n===== APIOT Dashboard Integration Test =====\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "network_state.json").write_text(json.dumps(NETWORK_STATE))
        (Path(tmpdir) / "attack_log.json").write_text(json.dumps(ATTACK_LOG))
        (Path(tmpdir) / "remediation_log.json").write_text(json.dumps(REMEDIATION_LOG))

        os.environ["APIOT_DATA_DIR"] = tmpdir

        if "interactive_lab" in sys.modules:
            del sys.modules["interactive_lab"]

        import interactive_lab
        interactive_lab.APIOT_DATA_DIR = Path(tmpdir)

        # ── Test 1: build_agent_view structure ──
        print("[*] Test 1: build_agent_view structure")
        view = interactive_lab.build_agent_view()
        check("Returns dict with 'hosts' key", "hosts" in view)
        hosts = view["hosts"]
        check("Host 192.168.100.35 present", "192.168.100.35" in hosts)

        h = hosts.get("192.168.100.35", {})

        # ── Test 2: Mapper data ──
        print("\n[*] Test 2: Mapper data")
        check("MAC populated", h.get("mac") == "00:00:94:00:83:00")
        check("Vendor populated", h.get("vendor") == "Asante Technologies")
        check("Ports populated", "4242" in h.get("ports", {}))
        port_info = h.get("ports", {}).get("4242", {})
        check("Port service is 'echo'", port_info.get("service") == "echo")

        # ── Test 3: Vulnerabilities ──
        print("\n[*] Test 3: Vulnerabilities")
        vulns = h.get("vulnerabilities", [])
        check("Has 1 vulnerability", len(vulns) == 1, f"got {len(vulns)}")
        if vulns:
            check("Vuln ID is 'abc123'", vulns[0].get("id") == "abc123")
            check("Vuln attack is 'crash_verified'", vulns[0].get("attack") == "crash_verified")

        # ── Test 4: Attack log ──
        print("\n[*] Test 4: Attack log")
        atk = h.get("attacks", {})
        check("Attack count is 2", atk.get("attack_count") == 2, f"got {atk.get('attack_count')}")
        check("Last tool is 'verify_crash'", atk.get("last_attack_tool") == "verify_crash")
        check("Last outcome is 'crash_verified'", atk.get("last_outcome") == "crash_verified")

        recent = h.get("recent_attacks", [])
        check("Recent attacks has 2 entries", len(recent) == 2, f"got {len(recent)}")

        # ── Test 5: Remediation ──
        print("\n[*] Test 5: Remediation")
        rem = h.get("remediation", {})
        check("Remediation present", bool(rem))
        check("Rule contains 'iptables'", "iptables" in rem.get("last_rule", ""))
        check("Applied is True", rem.get("applied") is True)
        check("Attack mitigated is 'coap_option_overflow'",
              rem.get("attack_mitigated") == "coap_option_overflow")

        # ── Test 6: No risk_level from backend ──
        print("\n[*] Test 6: Backend does NOT inject risk_level")
        check("risk_level NOT in host data",
              "risk_level" not in h,
              f"keys: {list(h.keys())}")

        # ── Test 7: Empty / missing APIOT data ──
        print("\n[*] Test 7: Empty / missing APIOT data")
        interactive_lab.APIOT_DATA_DIR = Path("/nonexistent/path")
        interactive_lab._apiot_warned = False
        view_empty = interactive_lab.build_agent_view()
        check("Missing data returns empty hosts", view_empty == {"hosts": {}})

        # ── Test 8: Flask endpoint /api/agent_state ──
        print("\n[*] Test 8: /api/agent_state endpoint")
        interactive_lab.APIOT_DATA_DIR = Path(tmpdir)
        with interactive_lab.app.test_client() as client:
            resp = client.get("/api/agent_state")
            check("Endpoint returns 200", resp.status_code == 200)
            body = resp.get_json()
            check("Response has 'hosts' key", "hosts" in body)
            check("Host data present in response",
                  "192.168.100.35" in body.get("hosts", {}))

        # ── Test 9: Endpoint with missing data ──
        print("\n[*] Test 9: /api/agent_state with no APIOT data")
        interactive_lab.APIOT_DATA_DIR = Path("/nonexistent/path")
        with interactive_lab.app.test_client() as client:
            resp = client.get("/api/agent_state")
            check("Endpoint still returns 200", resp.status_code == 200)
            body = resp.get_json()
            check("Response hosts is empty", body.get("hosts") == {})

        # ── Test 10: /topology endpoint (APIOT compat) ──
        print("\n[*] Test 10: /topology endpoint (APIOT compatibility)")
        with interactive_lab.app.test_client() as client:
            resp = client.get("/topology")
            check("/topology returns 200", resp.status_code == 200)
            check("/topology returns list", isinstance(resp.get_json(), list))

        # ── Test 11: /library endpoint (APIOT compat) ──
        print("\n[*] Test 11: /library endpoint (APIOT compatibility)")
        with interactive_lab.app.test_client() as client:
            resp = client.get("/library")
            check("/library returns 200", resp.status_code == 200)
            lib = resp.get_json()
            check("/library returns list", isinstance(lib, list))
            check("/library has firmware entries", len(lib) > 0, f"got {len(lib)}")

    # ── Summary ──
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"\n{'='*50}")
    print(f"  APIOT Integration: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

# APIOT ↔ iot_vlab Integration

This document describes how the **APIOT** autonomous security agent interacts
with the **iot_vlab** virtual lab — what channels it uses, what access it has,
and how the dashboard reflects its activity.

> **For the APIOT developer:** see [`apiot-contract.md`](apiot-contract.md) for
> the exact API contract, data file schemas, and timing requirements.

---

## Interaction Channels

APIOT operates over **two independent channels**:

| Channel | Direction | Purpose |
|---------|-----------|---------|
| REST API (`http://localhost:5000`) | APIOT → iot_vlab | Read-only topology and library discovery |
| Bridge networks (`br0`, `br_internal`) | APIOT → QEMU devices | All scanning, exploitation, and remediation |

The REST API is **never** used for attacks.  APIOT reads `/topology` and
`/library` to learn which devices exist, then operates entirely at L2/L3
against the devices' actual IPs on the bridge interfaces.

---

## Network Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Kali Host                             │
│                                                          │
│   ┌──────────┐       ┌───────────────────────────────┐  │
│   │  APIOT   │       │         iot_vlab               │  │
│   │  Agent   │       │  Flask API :5000               │  │
│   │          │       │  QEMU hypervisor               │  │
│   └────┬─────┘       └──────────┬────────────────────┘  │
│        │                        │                        │
│   ┌────▼────────────────────────▼───┐                   │
│   │  br0  (192.168.100.0/24)        │← dnsmasq DHCP    │
│   │  External / DMZ bridge          │                   │
│   └──┬────┬────┬────┬──────────────┘                   │
│      │    │    │    │                                    │
│    tap0 tap1 tap2  ...   ← QEMU device TAPs            │
│                                                          │
│   ┌─────────────────────────────────┐                   │
│   │  br_internal (192.168.200.0/24) │← dnsmasq DHCP    │
│   │  Manufacturing / OT zone        │                   │
│   └──┬────┬────┬───────────────────┘                   │
│      │    │    │                                         │
│   tap3  tap4  tap5   ← internal-only TAPs              │
│                                                          │
│   Multi-homed devices (segmented_gateway) have a TAP    │
│   on BOTH bridges simultaneously.                        │
└──────────────────────────────────────────────────────────┘
```

APIOT runs on the **same Kali host** that owns both bridges.  The host kernel
can route between them, so APIOT has full L2/L3 access to every device on
**both** `br0` and `br_internal` — exactly as a real attacker who has
compromised the network fabric would.

---

## Phase 1 — Discovery (Read-Only API)

APIOT's `LabClient` (`toolkit/lab_client.py`) connects to iot_vlab's Flask
server:

| Endpoint | Returns |
|----------|---------|
| `GET /topology` | Active VMs: IPs, MACs, firmware IDs, alive status, bridge |
| `GET /library`  | Available firmware profiles and their configs |

This is used **only** for:

- Confirming the lab is online (`ensure_lab_ready()`)
- Seeding the mapper with known device IPs
- Understanding what firmware is running (for attack selection)

`LabClient` never calls `/spawn`, `/kill`, or `/reset_lab`.

---

## Phase 2 — Reconnaissance (Direct Network Scanning)

The `NetworkMapper` (`core/mapper.py`) performs real nmap scans bound to each
bridge interface:

```
Subnet              Interface       Scan type
192.168.100.10-50   br0             nmap -sn (ping), -sV (TCP), -sU (UDP)
192.168.200.10-50   br_internal     nmap -sn (ping), -sV (TCP), -sU (UDP)
```

All nmap commands use `-e <interface>` to bind to the correct bridge.
Discovered hosts, open ports, and OS guesses are written to
`data/network_state.json`.

### Why APIOT can reach br_internal

APIOT doesn't need a TAP interface on `br_internal`.  Both bridges exist as
Linux bridge devices on the host.  The host's network stack has direct IP
connectivity to both subnets (`192.168.100.1` on `br0`, `192.168.200.1` on
`br_internal`), so any process on the host — including APIOT's nmap and
exploit scripts — can reach any device on either bridge.

---

## Phase 3 — Exploitation (Direct L2/L3 Attacks)

Attacks are performed with **raw sockets, HTTP, SSH, and shell tools** against
device IPs.  Nothing goes through the REST API.

### OT Exploits (`toolkit/ot_exploits.py`)

| Attack | Protocol | Target |
|--------|----------|--------|
| `modbus_write_coil` | TCP :502 | Writes to coil register via Modbus FC 0x05 |
| `modbus_mbap_overflow` | TCP :502 | Malformed MBAP header (declared length ≠ actual) |
| `coap_option_overflow` | UDP :5683 | CoAP packet with overflowed option delta/length |

### Linux Exploits (`toolkit/linux_exploits.py`)

| Attack | Protocol | Target |
|--------|----------|--------|
| `http_cmd_injection` | HTTP :80 | Shell metacharacter injection in GET/POST |
| `brute_force_telnet` | TCP :23 | Default credential guessing |
| `brute_force_ssh` | TCP :22 | `sshpass` + `ssh` credential stuffing |

### Dynamic Tools

The LLM agent can also:

- **Run arbitrary shell commands** via `run_command` (restricted by system
  prompt to lab subnets)
- **Create new exploit modules** at runtime via `create_tool`, which are saved
  to `toolkit/` and immediately registered

### Verification (`toolkit/verifier.py`)

After exploitation, APIOT verifies impact:

- `verify_crash` — ICMP ping + TCP handshake + CoAP probe to check if the
  device went down
- `verify_shell` — TCP connect, send command, check for marker string

All verification is also direct network access.

Every attack attempt is logged to `data/attack_log.json` with timestamp,
target IP, tool used, payload (hex), and outcome.

---

## Phase 4 — Remediation (Host iptables)

APIOT's Blue Agent (`toolkit/defender.py`, `core/verifier_blue.py`) applies
defensive patches:

1. **Generates iptables rules** based on attack signatures (e.g.,
   `iptables -A FORWARD -p udp --dport 5683 -m length --length 0:7 -j DROP`)
2. **Applies the rule** on the host's FORWARD chain via `sudo iptables`
3. **Replays the original exploit** to verify the patch blocks it
4. **Marks the vulnerability remediated** in `data/network_state.json`
5. Logs everything to `data/remediation_log.json`

The iptables rules operate on the **bridge FORWARD chain**, filtering traffic
as it crosses `br0` or `br_internal` — effectively acting as a virtual
firewall in front of the device.

---

## Data Files (APIOT → iot_vlab Dashboard)

APIOT writes three JSON files that iot_vlab's dashboard reads:

| File | Content | Dashboard use |
|------|---------|---------------|
| `data/network_state.json` | Discovered hosts, fingerprints, active vulnerabilities, remediation status | Node tooltips, risk colouring |
| `data/attack_log.json` | Every exploit attempt with timestamps and outcomes | Attack edges in topology, SSE log stream |
| `data/remediation_log.json` | Applied iptables rules and verification results | Green "patched" indicators |

The dashboard reads these files via `/api/agent_state` (polled every 2 s) and
derives visual risk levels purely on the frontend:

| Risk Level | Condition | Visual |
|------------|-----------|--------|
| `none` | No APIOT data for this IP | Default border |
| `recon` | Ports discovered, no attacks yet | Purple border |
| `attacked` | Attack attempts logged | Amber border, `[ATK]` label |
| `exploited` | Verified vulnerability exists | Red border, `[VULN]` label |
| `patched` | Remediation applied and verified | Green border, `[FIX]` label |

iot_vlab **never invents or overrides** security assessments — it only
visualises what APIOT reports.

---

## Summary

```
APIOT reads iot_vlab API ──→ topology + library (read-only)
APIOT scans bridge networks ──→ nmap on br0 + br_internal
APIOT attacks device IPs ──→ raw sockets, HTTP, SSH (direct L2/L3)
APIOT patches host iptables ──→ FORWARD chain rules on bridges
APIOT writes JSON files ──→ iot_vlab dashboard reads and visualises
```

Cross-bridge access (APIOT attacking `br_internal` devices) is expected
behaviour: both bridges are Linux bridge devices on the same host, and the
host kernel has IP addresses on both subnets.

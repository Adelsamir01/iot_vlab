# APIOT ↔ iot_vlab Integration Contract

**Purpose:** This document is the authoritative specification for how APIOT
must interact with iot_vlab. It defines the API contract, data file schemas,
timing expectations, and visual behaviour the iot_vlab dashboard relies on.
Hand this to the APIOT developer as the single source of truth.

---

## 1. Architecture Summary

```
┌──────────────────────────────────────────────────────────────┐
│  Kali Host                                                   │
│                                                              │
│  ┌───────────────┐   REST (read-only)   ┌────────────────┐  │
│  │    APIOT       │ ──────────────────►  │   iot_vlab     │  │
│  │    Agent       │                      │   Flask :5000  │  │
│  │                │   JSON files         │                │  │
│  │  data/*.json   │ ◄──(dashboard reads) │   Dashboard    │  │
│  └───────┬───────┘                      └───────┬────────┘  │
│          │ direct L2/L3                         │            │
│          │ (nmap, sockets, ssh)                  │            │
│  ┌───────▼──────────────────────────────────────▼────────┐  │
│  │  br0 (192.168.100.0/24)    br_internal (192.168.200.0/24)│
│  │      QEMU devices on TAP interfaces                    │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

**Two channels:**

| Channel | Direction | Purpose |
|---------|-----------|---------|
| REST API `http://localhost:5000` | APIOT → iot_vlab | Read-only: discover topology and firmware library |
| JSON data files (`data/`) | iot_vlab dashboard ← APIOT | Dashboard reads APIOT's output files to visualise activity |

APIOT **never** calls `/spawn`, `/kill`, or `/reset_lab`. It only reads.

---

## 2. REST API Endpoints APIOT Consumes

### `GET /topology`

Returns a JSON array of active devices.

```json
[
  {
    "id": "dvrf_v03_0f78c6dc",
    "firmware_id": "dvrf_v03",
    "arch": "mipsel",
    "name": "DVRF v0.3 (MIPS)",
    "pid": 39348,
    "tap": "tap0",
    "mac": "52:54:00:90:ab:96",
    "ip": "192.168.100.42",
    "alive": true,
    "bridge": "br0"
  }
]
```

Key fields for APIOT:
- `ip` — target IP for scanning/attacks. May be `"pending"` during boot.
- `alive` — `false` means QEMU process died (device crashed or was killed).
- `bridge` — which network segment the device is on (`"br0"` or `"br_internal"`).
- `arch` — helps select appropriate exploits (`"mipsel"`, `"armel"`, `"cortex-m3"`).

### `GET /library`

Returns a JSON array of available firmware profiles.

```json
[
  {
    "id": "dvrf_v03",
    "name": "DVRF v0.3 (MIPS)",
    "arch": "mipsel",
    "kernel": "vmlinux-3.2.0-4-malta",
    "rootfs": "rootfs.qcow2",
    "qemu_machine": "malta",
    "default_creds": "root:root"
  }
]
```

### `GET /api/ready`

Returns lab readiness status. APIOT should poll this before starting
operations.

```json
{ "ready": true, "total": 6, "pending": 0 }
```

**Wait for `ready: true` before scanning.** Devices with `ip: "pending"` are
still booting and will not respond to network probes.

---

## 3. Data Files APIOT Must Write

APIOT writes three JSON files to its `data/` directory. The iot_vlab dashboard
reads these files (configured via `APIOT_DATA_DIR` env var, default
`../apiot/data/`).

**Critical timing requirement:** The dashboard polls `/api/agent_state` every
2 seconds. Attack arrows on the topology graph are **transient** — they appear
only while `server_time - last_attack_time ≤ 15 seconds`. This means:

- APIOT must write `attack_log.json` **promptly** after each attack (not
  batched at the end of a session).
- Timestamps must be **Unix epoch floats** (`time.time()` in Python).
- If APIOT delays writing, the dashboard will never show the attack arrow.

### 3.1 `network_state.json`

Written/updated whenever APIOT discovers new information about the network.

```json
{
  "discovered_hosts": {
    "192.168.100.42": {
      "mac": "52:54:00:90:ab:96",
      "vendor": "QEMU virtual NIC"
    }
  },
  "fingerprints": {
    "192.168.100.42": {
      "ip": "192.168.100.42",
      "ports": {
        "22": {
          "state": "open",
          "protocol": "tcp",
          "service": "ssh",
          "version": "6.0p1 Debian 4"
        },
        "5683": {
          "state": "open",
          "protocol": "udp",
          "service": "coap",
          "version": ""
        }
      },
      "os_guess": "Linux (Debian-based)"
    }
  },
  "active_vulnerabilities": {
    "<unique_vuln_id>": {
      "ip": "192.168.100.35",
      "attack": "coap_option_overflow",
      "verification": {
        "status": "crashed",
        "verified": true,
        "details": "ICMP ping failed after exploit"
      },
      "timestamp": 1773075200.123
    }
  }
}
```

**Required fields per section:**

| Section | Required keys | Notes |
|---------|--------------|-------|
| `discovered_hosts[ip]` | `mac`, `vendor` | Written after ping sweep |
| `fingerprints[ip]` | `ip`, `ports` | Written after service scan. `os_guess` optional. |
| `fingerprints[ip].ports[port]` | `state`, `protocol`, `service` | `version` optional |
| `active_vulnerabilities[id]` | `ip`, `attack`, `timestamp` | `verification` optional but recommended |

**Dashboard behaviour:**
- Hosts in `discovered_hosts` or `fingerprints` → node gets purple border
  (recon state)
- Hosts in `active_vulnerabilities` → node gets red border + 🔓 icon

### 3.2 `attack_log.json`

**Append-only** JSON array. Each attack attempt appends one entry.

```json
[
  {
    "timestamp": 1773075154.012,
    "target_ip": "192.168.100.35",
    "tool_used": "coap_option_overflow",
    "outcome": "delivered",
    "packets_sent": 1,
    "details": { ... }
  }
]
```

**Required fields per entry:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `timestamp` | float | **YES** | Unix epoch. Dashboard uses this for transient attack arrows. |
| `target_ip` | string | **YES** | Must match an IP from `/topology`. |
| `tool_used` | string | **YES** | Displayed in node overlay and log stream. |
| `outcome` | string | **YES** | e.g. `"delivered"`, `"crash_verified"`, `"alive"`, `"timeout"` |
| `packets_sent` | int | no | |
| `target_arch` | string | no | |
| `payload_hex` | string | no | |
| `details` | object | no | Freeform details |

**Dashboard behaviour:**
- Node gets amber border + ⚡ icon when any attack exists for that IP
- Transient arrow appears from APIOT Agent → device node for 15 seconds after
  `timestamp`
- Arrow opacity fades linearly over the 15-second window
- After all arrows expire, the APIOT→device attack lines disappear; only the
  border/icon remains as an indicator of compromise

### 3.3 `remediation_log.json`

**Append-only** JSON array. Each remediation action appends one entry.

```json
[
  {
    "timestamp": 1773075213.589,
    "attack": "coap_option_overflow",
    "target_ip": "192.168.100.35",
    "rule": "iptables -A FORWARD -p udp --dport 5683 -m length --length 0:7 -j DROP",
    "applied": true,
    "elapsed_s": 0.035
  }
]
```

**Required fields per entry:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `timestamp` | float | **YES** | Unix epoch |
| `target_ip` | string | **YES** | Must match a device IP |
| `attack` | string | **YES** | Which attack this remediates |
| `rule` | string | **YES** | The iptables rule or remediation action |
| `applied` | bool | **YES** | Whether the rule was successfully applied |

**Dashboard behaviour:**
- Node gets green border + ✅ icon
- Overlay shows the applied rule

---

## 4. How the Dashboard Determines APIOT Presence

The dashboard calls `GET /api/agent_state` every 2 seconds. The response:

```json
{
  "active": true,
  "server_time": 1773112345.678,
  "hosts": { ... }
}
```

- `active: false` → **No APIOT visuals at all.** No agent node, no coloured
  borders, no attack arrows, no badge. Dashboard is pure iot_vlab.
- `active: true` → APIOT Agent diamond node appears on the topology, connected
  to both bridges. Header shows "APIOT Engaged" badge. Device nodes get risk
  indicators.

**Liveness detection:** The dashboard determines `active` by checking the
**modification time** of the three data files. If **none** of the files were
modified in the last **30 seconds**, `active` is `false`. This means:

- APIOT does not need to "register" with iot_vlab — just keep writing files.
- APIOT must write to at least one data file every ~30 seconds while active
  (normal scanning/attacking naturally achieves this).
- When APIOT stops, the dashboard automatically hides all APIOT visuals within
  30 seconds — no cleanup needed.
- Stale data files from previous sessions are ignored.

---

## 5. Visual State Machine (Per Device Node)

```
                     ┌──────────────┐
      No APIOT data  │    Default   │  Grey border, no icon
      for this IP    │    (none)    │
                     └──────┬───────┘
                            │ fingerprints[ip] appears
                            ▼
                     ┌──────────────┐
                     │    Recon     │  Purple border (#6366f1)
                     │              │
                     └──────┬───────┘
                            │ attack_log entry for this IP
                            ▼
                     ┌──────────────┐
                     │   Attacked   │  Amber border (#f59e0b) + ⚡
                     │              │  Transient arrow from Agent
                     └──────┬───────┘
                            │ active_vulnerabilities entry
                            ▼
                     ┌──────────────┐
                     │  Exploited   │  Red border (#ef4444) + 🔓
                     │              │
                     └──────┬───────┘
                            │ remediation_log entry
                            ▼
                     ┌──────────────┐
                     │   Patched    │  Green border (#10b981) + ✅
                     │              │
                     └──────────────┘
```

Priority (highest wins): patched > exploited > attacked > recon > none.

---

## 6. Transient Attack Arrows — Timing Detail

The dashboard renders a dashed arrow from the APIOT Agent node to the target
device node when:

```
server_time - attack_entry.last_attack_time ≤ 15 seconds
```

The arrow's opacity fades linearly: `opacity = 1 - (age / 15)`, clamped to a
minimum of 0.15.

**What this means for APIOT:**

- Write to `attack_log.json` **immediately** after each exploit attempt.
- Do NOT batch writes. Each attack should be visible on the dashboard within
  the next 2-second polling cycle.
- The dashboard will show the arrow pointing at whichever device was just
  attacked — so sequential attacks on different devices will show arrows
  "moving" to the current target.
- Old arrows disappear automatically. No cleanup needed from APIOT.

---

## 7. Lifecycle Expectations

### Startup

1. iot_vlab starts first (`sudo python3 interactive_lab.py`).
2. Wait for `GET /api/ready` to return `{"ready": true}`.
3. APIOT begins operations (scan, attack, remediate).
4. Dashboard automatically detects APIOT via non-empty data files.

### During Operation

- APIOT writes to `data/*.json` as it works.
- Dashboard polls every 2s and updates visuals.
- APIOT can attack any device on `br0` or `br_internal` — the host has direct
  L2/L3 access to both subnets.

### Shutdown

- APIOT stops writing. Data files remain.
- Dashboard continues to show the last known state (borders, icons) as long as
  files exist.
- To fully clear APIOT visuals: delete or empty all three JSON files.
- iot_vlab shutdown (`Ctrl+C`) cleans up QEMU instances and TAPs. APIOT data
  files are not touched.

---

## 8. What APIOT Should NOT Do

- **Never** call `/api/kill`, `/api/spawn`, or `/api/reset_lab`.
- **Never** modify iot_vlab's source files, configs, or database.
- **Never** assign risk levels or security assessments in `network_state.json`
  that iot_vlab should interpret — iot_vlab derives visual state purely from
  the presence/absence of data in the three files.
- **Never** write to iot_vlab's log directory or SSE stream directly — the
  dashboard picks up APIOT events from `attack_log.json` automatically.

---

## 9. Environment Variable

If APIOT's data directory is not at `../apiot/data/` relative to iot_vlab,
set:

```bash
export APIOT_DATA_DIR=/path/to/apiot/data
```

before starting iot_vlab.

---

## 10. Quick Checklist for APIOT Developer

- [ ] Write `network_state.json` after each scan phase (hosts, fingerprints, vulns)
- [ ] Append to `attack_log.json` immediately after each attack attempt
- [ ] Include `timestamp` (Unix float) and `target_ip` in every attack entry
- [ ] Append to `remediation_log.json` after each patch
- [ ] Poll `GET /api/ready` before starting — wait for `ready: true`
- [ ] Use `GET /topology` to discover device IPs (don't hardcode)
- [ ] Use `GET /library` to understand firmware profiles
- [ ] Test: launch iot_vlab → launch APIOT → verify agent diamond appears on dashboard
- [ ] Test: attack a device → verify transient arrow appears and fades
- [ ] Ensure at least one data file is written/updated every ~30s while running (liveness heartbeat)
- [ ] Test: stop APIOT → within 30s, dashboard returns to clean state (no need to delete files)

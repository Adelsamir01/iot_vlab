# IoT Virtual Security Lab

A **native** (no Docker) IoT firmware emulation lab for Kali Linux. Boots real router firmware and bare-metal MCU firmware inside QEMU, connects every device to a shared virtual network, and exposes a REST API so you can spawn, inspect, and tear down devices programmatically.

The lab is designed to be targeted by [APIOT](https://github.com/Adelsamir01/apiot) — an autonomous LLM-driven purple team agent — but can be used standalone for any IoT security testing.

Three device classes across multiple protocols:

- **MIPS Linux routers** — same architecture as consumer routers (Linksys, D-Link, TP-Link)
- **ARM Linux gateways** — representative of ARM-based IoT cameras, hubs, and embedded controllers
- **ARM Cortex-M3 industrial devices** — resource-constrained MCUs running Zephyr RTOS with bare-metal CoAP and Modbus/TCP stacks, simulating PLCs and field sensors

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Kali Linux Host                                                         │
│                                                                          │
│  ┌────────────┐   ┌──────────────────────────────────────────────────┐  │
│  │ lab_api.py │   │  br0  virtual bridge  192.168.100.1/24           │  │
│  │ REST :5000 │──▶│                                                  │  │
│  └────────────┘   │  tap0 ─── QEMU (MIPS Malta)    Linux router     │  │
│                   │  tap1 ─── QEMU (ARM VersatilePB) Linux GW       │  │
│  ┌────────────┐   │  tap2 ─── QEMU (ARM lm3s6965evb) Zephyr MCU    │  │
│  │simulators/ │   │                                                  │  │
│  │(Python)    │──▶│  .100+ ── coap_sim.py / modbus_sim.py           │  │
│  └────────────┘   │                                                  │  │
│                   │  dnsmasq ─ DHCP .10-.50 for QEMU guests          │  │
│                   │  iptables ─ NAT to internet                      │  │
│                   └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

QEMU devices get IPs in `.10-.50` via dnsmasq DHCP. Python simulators bind statically to `.100+` addresses. Both are accessible on the same bridge, so agents targeting the network see a mix of QEMU and Python-simulated devices.

---

## Supported Devices

### QEMU Firmware (via lab_api.py / interactive_lab.py)

| Firmware ID | Name | CPU | OS / Stack | Protocol / Port | Creds | Boot Time |
|---|---|---|---|---|---|---|
| `dvrf_v03` | Damn Vulnerable Router v0.3 | MIPS 4Kc | Debian Linux | SSH :22, HTTP :80 | `root:root` | 60-90 s |
| `debian_armel` | Debian Wheezy ARM | ARMv5TE | Debian Linux | SSH :22, HTTP :80 | `root:root` | 60-90 s |
| `zephyr_echo` | Industrial Sensor (Echo) | ARM Cortex-M3 | Zephyr RTOS 3.7 | TCP+UDP echo :4242 | none | 3-6 s |
| `zephyr_coap` | Smart Meter (CoAP) | ARM Cortex-M3 | Zephyr RTOS 3.7 | CoAP UDP :5683 | none | 5-8 s |
| `arm_modbus_sim` | PLC Valve Controller | ARM Cortex-M3 | Zephyr RTOS 3.7 | Modbus/TCP :502 | none | 5-8 s |

> **Stellaris MAC constraint:** The lm3s6965evb SoC has a hardcoded MAC address (`00:00:94:00:83:00`). Only **one** Cortex-M3 device can be on the bridge at a time.

### Python Simulators (via simulators/)

Lightweight Python processes that bind to static IPs on `br0`, crash-and-restart on malformed input, and expose the same protocols as their QEMU counterparts. Used in automated experiment suites where QEMU boot times would make 42 runs impractical.

| Simulator | Module | IP Pool | Protocol | Crash Trigger |
|---|---|---|---|---|
| CoAP | `simulators/coap_sim.py` | `.100-.149` | CoAP UDP :5683 | Option byte `0xDD` (delta=13, len=13 overflow) |
| Modbus | `simulators/modbus_sim.py` | `.150-.199` | Modbus/TCP :502 | MBAP `length` field ≥ 1000 |

Simulators have a configurable watchdog: if they do not receive traffic for N seconds after a crash, they auto-reset. The default watchdog is 60 s (matching the LLM response window).

The `sim_manager.py` module orchestrates multiple simulators together and integrates with `lab_api.py`'s `/topology` endpoint, so APIOT's network mapper sees simulators alongside QEMU devices.

```bash
# Start a single simulator manually
sudo python3 -m iot_vlab.simulators.coap_sim --ip 192.168.100.100 --port 5683

# Start via experiment runner (preferred)
python3 scripts/run_experiment.py --protocol coap --topology T1 ...
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Kali Linux** (or Debian-based) | Tested on Kali 6.16.8 aarch64 |
| **sudo access** | Required for bridge/TAP networking and QEMU |
| **~600 MB disk** | For the Linux firmware images |
| **Python 3.10+** | With `flask`, `requests` |
| **Zephyr toolchain** (optional) | Only needed to rebuild MCU firmware from source |

### Why `sudo` is required

- `lab_manager.py` creates/destroys TAP interfaces via `ip tuntap` and `ip link`
- `setup_network.sh` and `impair_network.sh` configure bridges, DHCP, NAT, and `tc netem`
- `interactive_lab.py` applies realism features (`tc` rules, background HMI traffic)

The REST API and simulators themselves do not require elevated privileges; only the QEMU/TAP operations do.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Adelsamir01/iot_vlab.git
cd iot_vlab
```

### 2. Install Python dependencies

```bash
pip3 install flask requests
# On Kali you may need:
pip3 install --break-system-packages flask requests
```

### 3. Set up the host network

Creates the `br0` bridge, starts dnsmasq DHCP, configures NAT, and installs QEMU packages.

```bash
sudo ./setup_network.sh
```

Idempotent — safe to run multiple times.

### 4. Download Linux firmware images

```bash
./download_firmware.sh
```

Downloads MIPS and ARM kernels + root filesystems (~600 MB) into `library/`.

### 5. (Optional) Set up the Zephyr toolchain

Only needed to rebuild MCU firmware from source. Pre-built ELFs are included.

```bash
./setup_zephyr.sh
```

Installs cmake, ninja, gperf, `west`, Zephyr SDK v0.16.8, and Zephyr v3.7.0 (~1-2 GB download).

### 6. (Optional) Rebuild MCU firmware

```bash
./build_sensor_firmware.sh     # Zephyr echo server → library/zephyr_echo/
./build_advanced_firmware.sh   # CoAP + Modbus → library/zephyr_coap/ and library/arm_modbus_sim/
```

### 7. Verify the infrastructure

```bash
python3 verify_lab.py
```

---

## Usage

### Option A: Interactive wizard + Web Dashboard (recommended)

```bash
sudo python3 interactive_lab.py
```

The wizard asks which topology to spin up:

1. **Custom / Star Architecture** — specify device counts manually
2. **15-Node Realistic Mesh** — interconnected mesh with live `MeshTrafficGenerator`
3. **Purdue Model / Segmented IIoT** — DMZ router + multi-homed gateway + isolated OT cell on `br_internal`
4. **Edge-Fog-Cloud (Three-Tier) IIoT** — cloud layer (2 routers) + fog layer (4 gateways) + edge layer (Modbus PLC + 3 sensors)

Then whether to apply network impairments (latency/jitter/loss via `tc`) and background HMI traffic (Poisson-distributed Modbus/CoAP noise).

**Open `http://localhost:5000`** to view the live dashboard: streaming QEMU logs, interactive Vis.js topology, and per-node device details.

Press **Ctrl+C** to cleanly shut down all instances.

### Option B: REST API only

```bash
sudo python3 lab_api.py
```

Then from another terminal:

```bash
# List firmware library
curl -s http://localhost:5000/library | python3 -m json.tool

# Spawn a device (one cortex-m3 at a time)
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "zephyr_coap"}'

# View live topology
curl -s http://localhost:5000/topology | python3 -m json.tool

# Stop one device
curl -s -X POST http://localhost:5000/kill/<run_id>

# Stop everything
curl -s -X POST http://localhost:5000/reset_lab
```

### Option C: Talk to devices directly

```bash
# Echo server (port 4242)
echo "Hello Industrial" | nc <ip> 4242

# CoAP server (port 5683) — GET request
echo -ne '\x40\x01\x00\x01' | nc -u -w2 <ip> 5683

# Modbus/TCP (port 502) — read coils FC01
echo -ne '\x00\x01\x00\x00\x00\x06\x01\x01\x00\x00\x00\x08' | nc <ip> 502
```

---

## API Reference

| Method | Endpoint | Body | Response | Description |
|---|---|---|---|---|
| `GET` | `/library` | — | `[{id, name, arch, ...}]` | List all firmware profiles |
| `GET` | `/topology` | — | `[{id, firmware_id, ip, mac, pid, alive}]` | All running devices (QEMU + simulators) |
| `GET` | `/ready` | — | `{"ready": true/false}` | Lab readiness check |
| `POST` | `/spawn` | `{"firmware_id": "..."}` | `{"run_id": "..."}` (201) | Boot a new QEMU instance |
| `POST` | `/kill/<run_id>` | — | `{"status": "stopped"}` | Stop one instance |
| `POST` | `/reset_lab` | — | `{"status": "reset", "stopped": N}` | Kill all instances |

---

## Network Details

| Property | Value |
|---|---|
| Bridge interface | `br0` |
| Host IP (gateway) | `192.168.100.1` |
| QEMU DHCP range | `192.168.100.10` – `192.168.100.50` |
| Simulator IPs (CoAP) | `192.168.100.100` – `.149` |
| Simulator IPs (Modbus) | `192.168.100.150` – `.199` |
| Internal bridge (Purdue) | `br_internal` / `192.168.200.0/24` |
| Lease time | 12 hours |
| NAT | MASQUERADE via host's default interface |

---

## Project Structure

```
.
├── lab_api.py                  # Flask REST API (port 5000)
├── lab_manager.py              # Core QEMU process lifecycle + TAP management
├── interactive_lab.py          # Interactive wizard + web dashboard
├── scan_library.py             # Load firmware configs from library/
├── demo_network.py             # Standalone multi-arch demo script
├── verify_lab.py               # Infrastructure self-test (no sudo needed)
│
├── simulators/                 # Python-based protocol simulators
│   ├── coap_sim.py             # CoAP UDP server with crash/watchdog semantics
│   ├── modbus_sim.py           # Modbus/TCP server with MBAP overflow crash
│   └── sim_manager.py          # Multi-simulator orchestrator + topology bridge
│
├── setup_network.sh            # Create br0 bridge, dnsmasq, NAT, install QEMU
├── setup_zephyr.sh             # Install Zephyr SDK + source tree
├── download_firmware.sh        # Download MIPS + ARM Linux firmware
├── build_sensor_firmware.sh    # Compile Zephyr echo_server for Cortex-M3
├── build_advanced_firmware.sh  # Compile CoAP server + Modbus for Cortex-M3
├── impair_network.sh           # tc netem: apply/clear latency, jitter, loss
├── industrial_hmi_sim.py       # Background HMI traffic generator (Poisson)
│
├── library/
│   ├── dvrf_v03/               # MIPS Linux router (vmlinux + rootfs.img)
│   ├── debian_armel/           # ARM Linux gateway (vmlinuz + rootfs.qcow2)
│   ├── zephyr_echo/            # Zephyr TCP+UDP echo :4242 (zephyr.elf)
│   ├── zephyr_coap/            # Zephyr CoAP :5683 (zephyr.elf)
│   └── arm_modbus_sim/         # Zephyr Modbus/TCP :502 (zephyr.elf)
│
└── tests/
    ├── test_phase2.py          # Multi-device orchestration
    ├── test_phase2_5.py        # Cortex-M3 / Zephyr echo (12 tests)
    └── test_phase2_6.py        # CoAP + Modbus protocol (23 tests)
```

---

## Running Tests

```bash
# Infrastructure checks — no sudo needed
python3 verify_lab.py

# Multi-device orchestration
sudo python3 tests/test_phase2.py

# Cortex-M3 echo verification (12 tests)
sudo python3 tests/test_phase2_5.py

# CoAP + Modbus protocol expansion (23 tests)
sudo python3 tests/test_phase2_6.py

# Network impairments + HMI simulator (11 tests)
bash test_realism_features.sh
```

---

## Network Impairments

Simulate degraded conditions found in real industrial sites:

```bash
# Apply packet loss
sudo ./impair_network.sh --loss 5

# Apply latency with jitter
sudo ./impair_network.sh --jitter 50 20

# Clear all impairments
sudo ./impair_network.sh --clear

# Check current status
sudo ./impair_network.sh --status
```

To combine loss and jitter together, use `tc` directly:

```bash
sudo tc qdisc add dev br0 root netem loss 5% delay 50ms 20ms
```

---

## Adding Your Own Firmware

### Linux-based (MIPS or ARM)

Create `library/my_firmware/config.json`:

```json
{
    "id": "my_firmware",
    "name": "My Custom Firmware",
    "arch": "mipsel",
    "kernel": "vmlinux-custom",
    "rootfs": "rootfs-custom.qcow2",
    "qemu_machine": "malta",
    "default_creds": "admin:admin"
}
```

### Bare-metal MCU (Cortex-M3)

Create `library/my_mcu_app/config.json`:

```json
{
    "id": "my_mcu_app",
    "name": "My MCU Application",
    "arch": "cortex-m3",
    "kernel": "my_app.elf",
    "rootfs": null,
    "qemu_machine": "lm3s6965evb",
    "default_creds": "none",
    "net_model": "stellaris"
}
```

New firmware is picked up automatically on the next API/demo run.

---

## APIOT Integration

APIOT queries `/topology` and `/library` for read-only reconnaissance, then operates at L3 directly on `br0`. It does **not** use the REST API for attack operations. Contract:

- APIOT only calls `GET /topology`, `GET /library`, `GET /ready`
- APIOT never calls `/spawn`, `/kill`, or `/reset_lab`
- APIOT sees both QEMU devices (IPs `.10-.50`) and Python simulators (IPs `.100-.199`) via `/topology`
- APIOT applies `iptables` rules on the host as blue-team defenses — experiment runner flushes these between runs

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `br0` doesn't exist | Re-run `sudo ./setup_network.sh` |
| QEMU not found | Re-run `sudo ./setup_network.sh` (installs packages) |
| Linux firmware files missing | Run `./download_firmware.sh` |
| Zephyr firmware missing | Run `./build_sensor_firmware.sh` or `./build_advanced_firmware.sh` |
| Linux guest has no IP | Wait 60-90 s; check `cat /var/lib/misc/dnsmasq-br0.leases` |
| Zephyr guest has no IP | Boots in ~3-8 s; check `pgrep -a dnsmasq` |
| Second MCU device blocked | Only one cortex-m3 at a time (Stellaris MAC constraint) |
| Simulator crashes immediately | Check `ip addr show br0`; ensure `.100` range is reachable on br0 |
| Stale simulator in `/topology` | Delete `data/sim_topology.json` and restart lab_api.py |
| `iptables` blocking experiment traffic | Run `sudo iptables -F FORWARD && sudo iptables -F INPUT` |
| QEMU crashes immediately | Check `logs/qemu-*.log` |
| dnsmasq died | Re-run `sudo ./setup_network.sh` |

---

## License

MIT

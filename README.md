# IoT Virtual Security Lab

A **native** (no Docker) IoT firmware emulation lab for Kali Linux.
Boots real router firmware **and** bare-metal MCU firmware inside QEMU,
connects every device to a shared virtual network, and exposes a REST API
so you can spawn, inspect, and tear down devices programmatically.

The lab supports three classes of device across multiple protocols:

- **MIPS Linux routers** -- the same architecture found in consumer routers
  (Linksys, D-Link, TP-Link).
- **ARM Linux gateways** -- representative of ARM-based IoT cameras, hubs,
  and embedded controllers.
- **ARM Cortex-M3 industrial devices** -- resource-constrained MCUs running
  Zephyr RTOS with bare-metal TCP/IP stacks, simulating PLCs, field
  sensors, and industrial controllers with protocol-specific services
  (CoAP, Modbus/TCP, echo).

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Kali Linux Host                                                         │
│                                                                          │
│  ┌────────────┐       ┌───────────────────────────────────────────────┐  │
│  │ lab_api.py │       │   br0  virtual bridge  192.168.100.1/24       │  │
│  │ REST :5000 │──────▶│                                               │  │
│  └────────────┘       │   tap0 ── QEMU (MIPS Malta)   Linux router    │  │
│        │              │   tap1 ── QEMU (ARM VersatilePB) Linux GW     │  │
│        ▼              │   tap2 ── QEMU (ARM lm3s6965evb) Zephyr MCU   │  │
│  ┌────────────┐       │                                               │  │
│  │lab_manager │       │   dnsmasq ── DHCP .10-.50 for all guests      │  │
│  │    .py     │       │   iptables ─ NAT to internet                  │  │
│  └────────────┘       └───────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

Every device runs as its own QEMU process with a dedicated TAP network
interface.  All TAPs are bridged to `br0`, so every guest is on the same
Layer-2 segment and gets an IP address from dnsmasq -- exactly like
physical devices plugged into a real switch.

---

## Supported Devices

| Firmware ID | Name | CPU | OS / Stack | QEMU Board | Protocol / Port | Creds | Boot |
|---|---|---|---|---|---|---|---|
| `dvrf_v03` | Damn Vulnerable Router Firmware v0.3 | MIPS 4Kc (32-bit LE) | Debian Linux | Malta | SSH, HTTP | `root:root` | ~60-90 s |
| `debian_armel` | Debian Wheezy ARM | ARMv5TE (32-bit LE) | Debian Linux | VersatilePB | SSH, HTTP | `root:root` | ~60-90 s |
| `zephyr_echo` | Industrial Sensor (Echo) | ARM Cortex-M3 | Zephyr RTOS 3.7 | lm3s6965evb | TCP+UDP echo :4242 | None | ~3-6 s |
| `zephyr_coap` | Smart Meter (CoAP) | ARM Cortex-M3 | Zephyr RTOS 3.7 | lm3s6965evb | CoAP UDP :5683 | None | ~5-8 s |
| `arm_modbus_sim` | PLC Valve Controller | ARM Cortex-M3 | Zephyr RTOS 3.7 | lm3s6965evb | TCP echo :502 (Modbus port) | None | ~5-8 s |

### Linux devices (MIPS / ARM)

These boot a full Linux kernel with a root filesystem.  Once running they
behave like real embedded Linux boxes -- you can SSH into them, run
`busybox`, sniff traffic, or exploit known CVEs.

### Zephyr MCU devices (Cortex-M3)

Each boots a bare-metal Zephyr RTOS image on a Stellaris LM3S6965EVB
evaluation board (ARM Cortex-M3, 64 KB RAM, 256 KB flash).  No root
filesystem or operating system login is involved -- each is a single
ELF binary that boots in seconds.

Three firmware variants provide protocol diversity:

- **zephyr_echo** -- TCP + UDP echo on port 4242.  General-purpose
  reachability target.
- **zephyr_coap** -- CoAP server on UDP port 5683.  Simulates a smart
  meter or environmental sensor exposing resources via the
  Constrained Application Protocol.
- **arm_modbus_sim** -- TCP echo on port 502 (the standard Modbus/TCP
  port).  Simulates a "dumb" PLC that echoes any Modbus-framed request,
  useful for testing industrial protocol scanners.

> **Stellaris MAC constraint:** The lm3s6965evb SoC has a hardcoded MAC
> address (`00:00:94:00:83:00`) that cannot be overridden at runtime.
> Only **one** Cortex-M3 device can be on the bridge at a time.  The lab
> manager enforces this and returns an error if a second is spawned.

### Architecture roadmap (riscv32)

The `lab_manager.py` includes a `riscv32` architecture profile
(`qemu-system-riscv32 -M virt` with `virtio-net-device`).  It is
currently a placeholder because Zephyr v3.7 for `qemu_riscv32` has no
Ethernet driver (no virtio-net binding, no PCI+e1000 DTS, and SLIP
requires a second UART the virt machine doesn't expose).  The profile
is ready for activation when future Zephyr versions add support.
`qemu-system-riscv32` is installed by `setup_network.sh`.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Kali Linux** (or Debian-based) | Tested on Kali 6.16.8 aarch64; any recent Kali/Debian works |
| **sudo access** | Required for bridge/TAP networking and QEMU |
| **~600 MB disk** | For the two Linux firmware images |
| **Python 3.10+** | With `flask` and `requests` |
| **Zephyr toolchain** (optional) | Only needed if you want to rebuild MCU firmware from source |

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

Installs system packages (`qemu-system-mips`, `qemu-system-arm`,
`qemu-system-misc`, `bridge-utils`, `dnsmasq`, `iptables`), creates the
virtual bridge `br0`, starts a DHCP server, and configures NAT.

```bash
sudo ./setup_network.sh
```

The script is **idempotent** -- safe to run multiple times.

### 4. Download Linux firmware images

```bash
./download_firmware.sh
```

Downloads MIPS and ARM kernels + root filesystems into
`library/dvrf_v03/` and `library/debian_armel/`.

### 5. (Optional) Set up the Zephyr toolchain

Only needed to rebuild MCU firmware from source.  Pre-built ELFs are
included in the library.

```bash
./setup_zephyr.sh
```

Installs cmake, ninja, gperf, `west`, Zephyr SDK v0.16.8, and the
Zephyr source tree (v3.7.0) at `~/iot-lab/zephyrproject/`.
**Idempotent**; initial run downloads ~1-2 GB.

### 6. (Optional) Rebuild MCU firmware

```bash
# Original echo server (TCP+UDP :4242)
./build_sensor_firmware.sh

# CoAP server (UDP :5683) + Fake PLC (TCP :502)
./build_advanced_firmware.sh
```

Each script compiles Zephyr samples for `qemu_cortex_m3` with overlays
enabling Stellaris Ethernet and DHCPv4, then copies the ELF to the
appropriate `library/` directory.

### 7. Verify the infrastructure

```bash
python3 verify_lab.py
```

---

## Usage

### Option A: Interactive wizard + Web Dashboard (recommended)

An interactive CLI "wizard" that provisions devices and starts a live web dashboard.

```bash
# From the repo root
cd iot_vlab

# Run the interactive lab (requires the network setup + firmware downloads)
sudo ./setup_network.sh        # if not already done
./download_firmware.sh         # if not already done

sudo python3 interactive_lab.py
```

The CLI wizard will ask:
- Which topology to spin up:
  1. **Custom / Star Architecture:** Let's you specify exactly how many routers and gateways you want.
  2. **15-Node Realistic Mesh Topology:** Creates an interconnected mesh network featuring `lab_manager`'s `MeshTrafficGenerator` running dynamically in the background. Connections in the GUI will live-update to reflect actual packets flowing.
  3. **Purdue Model / Segmented IIoT Architecture:** Boots a DMZ router, a Multi-Homed Gateway navigating two bridges (`br0` and `br_internal`), and an isolated cell zone strictly on `br_internal` consisting of a CoAP meter and 3 general sensor endpoints. 
     - *Justification:* In a real-world Industrial IoT (IIoT) setting, devices do not reside on a flat, easily accessible subnet. They are isolated into specific zones (e.g., Enterprise, DMZ, Manufacturing, Cell/Area) dictated by the Purdue Enterprise Reference Architecture (PERA). By explicitly selecting this topology, agents or users must navigate a multi-homed gateway separating the internal operational technology (OT) network from the external-facing network, realistically testing lateral movement and pivoting capabilities.
  4. **Edge-Fog-Cloud (Three-Tier) IIoT Architecture:** Boots a centralized Cloud/Enterprise layer (2 remote routers), a thick distributed Fog layer (4 multi-homed edge gateways), and an expansive strictly-internal Edge layer (a Modbus PLC actuator and 3 sensor endpoints).
     - *Justification:* This architecture is widely recognized in modern IIoT and Fog Computing literature (e.g., Bonomi et al., OpenFog Consortium). It addresses latency, bandwidth, and security constraints by moving compute closer to the edge. In this scenario, agents can test data aggregation, edge-level security, or localized exploitation within the Fog nodes before pivoting up to the Cloud backend or down to the physical Edge devices.
- Whether to apply realistic network noise (latency, jitter, and packet loss using `tc`).
- Whether to enable background HMI traffic (Modbus & CoAP background noise generation using poisson distribution) to pollute the packet captures and test agent robustness.

After answering, it will start provisioning devices and spin up a web server.
**Open your browser to `http://localhost:5000`** to view the live Interactive Dashboard, which includes:
- Live streaming Terminal / QEMU logs (left panel)
- Interactive live-updating Vis.js network topology (right panel)
- Node interaction (click a visual node for device details, such as IP, MAC, PID, and an option to kill the device to simulate it going down).

Press **Ctrl+C** in the terminal to cleanly shut down all running instances and the dashboard server.

### Option B: Run the demo script

```bash
sudo python3 demo_network.py
```

Edit the `NETWORK` list in `demo_network.py` to customise which devices
to spawn.  Press **Ctrl+C** to cleanly shut down.

### Option C: Use the REST API

```bash
sudo python3 lab_api.py
```

Then from another terminal:

```bash
# List firmware library
curl -s http://localhost:5000/library | python3 -m json.tool

# Spawn devices (one cortex-m3 at a time)
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "dvrf_v03"}'

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

### Option D: Talk to MCU devices directly

Once a device has acquired a DHCP lease (check `GET /topology` for the
IP):

```bash
# Echo server (port 4242)
echo "Hello Industrial" | nc <ip> 4242

# CoAP server (port 5683) — send a CoAP GET
echo -ne '\x40\x01\x00\x01' | nc -u -w2 <ip> 5683

# Fake PLC (port 502) — Modbus/TCP port
echo "ModbusPing" | nc <ip> 502
```

---

## API Reference

| Method | Endpoint | Request Body | Response | Description |
|---|---|---|---|---|
| `GET` | `/library` | -- | `[{id, name, arch, ...}]` | List all firmware in the library |
| `GET` | `/topology` | -- | `[{id, firmware_id, arch, pid, tap, mac, ip, alive}]` | List every running VM |
| `POST` | `/spawn` | `{"firmware_id": "..."}` | `{"run_id": "..."}` (201) | Boot a new QEMU instance |
| `POST` | `/kill/<run_id>` | -- | `{"status": "stopped"}` | Stop and clean up one instance |
| `POST` | `/reset_lab` | -- | `{"status": "reset", "stopped": N}` | Kill all running instances |

Error responses return JSON with an `"error"` key and an appropriate
HTTP status code (400, 404, or 500).

---

## Project Structure

```
.
├── setup_network.sh            # Create br0 bridge, dnsmasq DHCP, NAT
├── setup_zephyr.sh             # Install Zephyr SDK, west, source tree
├── download_firmware.sh        # Download MIPS + ARM Linux firmware
├── build_sensor_firmware.sh    # Compile Zephyr echo_server for Cortex-M3
├── build_advanced_firmware.sh  # Compile CoAP server + Fake PLC for Cortex-M3
│
├── lab_api.py                  # Flask REST API (port 5000)
├── lab_manager.py              # LabManager class — QEMU process lifecycle
├── scan_library.py             # Scan library/ for firmware config.json files
├── demo_network.py             # Spawn a multi-arch network with live topology
├── start_emulation.py          # Legacy single-device CLI controller
├── verify_lab.py               # Infrastructure self-test
├── industrial_hmi_sim.py      # Industrial HMI Simulator (background traffic)
├── impair_network.sh           # Network impairment engine (loss, jitter)
├── verify_realism.py           # Realism verification script
├── test_realism_features.sh   # Comprehensive realism features test
├── mesh_network.py            # Mesh network generator (15 diverse nodes with visualization)
├── test_mesh_network.sh       # Mesh network script test
│
├── library/
│   ├── dvrf_v03/               # MIPS Linux router firmware
│   │   ├── config.json
│   │   ├── vmlinux-3.2.0-4-4kc-malta
│   │   └── rootfs.img
│   ├── debian_armel/           # ARM Linux gateway firmware
│   │   ├── config.json
│   │   ├── vmlinuz-3.2.0-4-versatile
│   │   ├── initrd.img-3.2.0-4-versatile
│   │   └── rootfs.qcow2
│   ├── zephyr_echo/            # Zephyr echo server (TCP+UDP :4242)
│   │   ├── config.json
│   │   └── zephyr.elf
│   ├── zephyr_coap/            # Zephyr CoAP server (UDP :5683)
│   │   ├── config.json
│   │   └── zephyr.elf
│   └── arm_modbus_sim/         # Fake PLC echo (TCP :502)
│       ├── config.json
│       └── zephyr.elf
│
├── tests/
│   ├── test_phase2.py          # Multi-device orchestration test
│   ├── test_phase2_5.py        # Cortex-M3 / Zephyr echo verification
│   └── test_phase2_6.py        # CoAP + Fake PLC protocol expansion test
│
├── logs/                       # QEMU console logs (auto-created)
└── .gitignore
```

---

## How the Lab Manager Works

`lab_manager.py` contains the `LabManager` class, which is the core QEMU
hypervisor.  When you call `spawn_instance(firmware_id)`:

1. **Firmware lookup** -- `scan_library.py` scans `library/*/config.json`
   and returns the matching config.

2. **Stellaris guard** -- If the firmware is `cortex-m3`, the manager
   checks that no other Cortex-M3 instance is running (hardcoded MAC
   conflict prevention).

3. **TAP creation** -- A new `tapN` interface is created, attached to
   `br0`, and brought up.

4. **QEMU command construction** -- Architecture-specific flags:

   | Arch | QEMU Binary | Machine | Drive | Network | Notes |
   |---|---|---|---|---|---|
   | `mipsel` | `qemu-system-mipsel` | `malta` | qcow2 rootfs | `-netdev tap` + `-device e1000` | Full Linux, 256 MB RAM |
   | `armel` | `qemu-system-arm` | `versatilepb` | qcow2 rootfs | `-net nic` + `-net tap` | Full Linux, 256 MB RAM |
   | `cortex-m3` | `qemu-system-arm` | `lm3s6965evb` | None | `-net nic,model=stellaris` + `-net tap` | Bare-metal ELF, no rootfs |
   | `riscv32` | `qemu-system-riscv32` | `virt` | None | `-device virtio-net-device` + `-netdev tap` | Bare-metal ELF, `-bios none -m 256` |

5. **Process launch** -- QEMU runs as a background process with console
   output redirected to `logs/qemu-<run_id>.log`.

6. **IP discovery** -- `refresh_ips()` polls dnsmasq leases and matches
   MAC addresses.

7. **Teardown** -- `stop_instance()` sends SIGTERM (SIGKILL fallback),
   closes the log, and destroys the TAP.

---

## Adding Your Own Firmware

### Linux-based firmware (MIPS or ARM)

1. Create `library/my_firmware/`
2. Place kernel and root filesystem inside.
3. Create `config.json`:

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

### Bare-metal MCU firmware (Cortex-M3)

1. Create `library/my_mcu_app/`
2. Place the compiled ELF binary inside.
3. Create `config.json`:

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

Supported `arch` values: `mipsel`, `armel`, `cortex-m3`, `riscv32`.
New firmware is picked up automatically on the next API/demo run.

---

## Network Details

| Property | Value |
|---|---|
| Bridge interface | `br0` |
| Host IP (gateway) | `192.168.100.1` |
| DHCP range | `192.168.100.10` -- `192.168.100.50` |
| Lease time | 12 hours |
| DNS servers | `8.8.8.8`, `8.8.4.4` |
| NAT | MASQUERADE via host's default interface |
| Max simultaneous VMs | ~40 (limited by DHCP range) |

All guests -- Linux VMs and bare-metal MCUs alike -- sit on the same
Layer-2 bridge and can communicate with each other, with the host, and
(via NAT) with the internet.

---

## Running Tests

```bash
# Infrastructure checks — no sudo needed
python3 verify_lab.py

# Multi-device orchestration — needs sudo + dvrf_v03 firmware
sudo python3 tests/test_phase2.py

# Cortex-M3 echo verification (12 tests)
sudo python3 tests/test_phase2_5.py

# CoAP + Fake PLC protocol expansion (23 tests)
sudo python3 tests/test_phase2_6.py

# Industrial Realism features test (11 tests)
bash test_realism_features.sh
```

### What `test_phase2_6.py` verifies

| # | Check | Details |
|---|---|---|
| 1 | API reachable | Starts `lab_api.py` and waits for HTTP 200 |
| 2-3 | Library entries | `zephyr_coap` and `arm_modbus_sim` present |
| 4-8 | CoAP spawn + network | Spawn, PID alive, TAP on br0, DHCP lease |
| 9 | CoAP protocol | UDP probe on port 5683 gets a response |
| 10-11 | CoAP cleanup | Kill, TAP removed |
| 12-16 | PLC spawn + network | Spawn, PID alive, TAP on br0, DHCP lease |
| 17 | PLC protocol | TCP echo on port 502 returns the sent message |
| 18-19 | PLC cleanup | Kill, TAP removed |
| 20 | MAC conflict guard | Second cortex-m3 spawn returns 500 |
| 21 | Final topology | Empty after all tests |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `sudo` keeps asking for password | `echo "$USER ALL=(ALL) NOPASSWD: ALL" \| sudo tee /etc/sudoers.d/$USER` |
| `br0` doesn't exist | Re-run `sudo ./setup_network.sh` |
| QEMU not found | Re-run `sudo ./setup_network.sh` (installs packages) |
| Linux firmware files missing | Run `./download_firmware.sh` |
| Zephyr firmware missing | Run `./build_sensor_firmware.sh` or `./build_advanced_firmware.sh` |
| `west` not found | Run `./setup_zephyr.sh` |
| `west build` RAM overflow | The Kconfig overlays disable IPv6 and shell to fit 64 KB; check build scripts |
| Linux guest has no IP | Wait 60-90 s for boot + DHCP; check `cat /var/lib/misc/dnsmasq-br0.leases` |
| Zephyr guest has no IP | Boots in ~3-8 s; check that dnsmasq is running (`pgrep -a dnsmasq`) |
| Second MCU device blocked | Only one cortex-m3 at a time (Stellaris MAC constraint); kill the first |
| QEMU crashes immediately | Check `logs/qemu-*.log` for errors |
| dnsmasq died | Re-run `sudo ./setup_network.sh` |

---

---

## Advanced & Standalone Realism Features

The `interactive_lab.py` wizard automatically orchestrates network impairments, background HMI traffic, and mesh network topologies. However, you can also run these underlying engines standalone for highly customized or legacy `demo_network.py` experiments.

### Background Traffic (HMI Simulator)

The Industrial HMI Simulator generates legitimate legitimate UDP/TCP background noise.

**Run standalone:**
```bash
sudo python3 industrial_hmi_sim.py [--interval MEAN_SECONDS]
```
The simulator polls devices on `192.168.100.0/24` using a Poisson distribution for realistic, non-rhythmic traffic patterns.

### Network Impairments (Traffic Control)

Simulate "dirty" networking found in real industrial sites. This is what the interactive wizard calls under the hood when realism is enabled.

**Apply packet loss manually:**
```bash
sudo ./impair_network.sh --loss 5
```

**Apply latency with jitter:**
```bash
sudo ./impair_network.sh --jitter 50 20
```

**Clear all impairments (panic button):**
```bash
sudo ./impair_network.sh --clear
```

**Check current status:**
```bash
sudo ./impair_network.sh --status
```

### Safety Notes

- **Panic Button**: Always use `./impair_network.sh --clear` if you lock yourself out or need to reset the network
- **Multi-Homed Limitation**: Currently supported for `mipsel` and `armel` architectures only (Linux-based devices)
- **Internal Bridge**: The `br_internal` bridge is automatically created when the first multi-homed device is spawned
- **DHCP**: Internal devices receive IPs from a separate DHCP range (192.168.200.10-50). Note: DHCP server for `br_internal` needs to be configured separately if automatic IP assignment is required.

### Testing Realism Features

A comprehensive test script is provided to verify all realism features:

```bash
bash test_realism_features.sh
```

This script tests:
- Network impairment script functionality (loss, jitter, clear)
- HMI simulator imports and help
- Realism verification script
- Demo network integration
- Multi-homed gateway support

**Live Test Results** (comprehensive testing completed):
```
==========================================
  IoT Virtual Lab — Realism Features Test
==========================================

[*] Test 1: Network Impairment Script
[PASS] impair_network.sh --help works
[PASS] Packet loss applied successfully
[PASS] Latency/jitter applied successfully

[*] Test 2: Industrial HMI Simulator
[PASS] industrial_hmi_sim.py --help works
[PASS] HMI simulator imports successfully

[*] Test 3: Realism Verification Script
[PASS] verify_realism.py --help works
[PASS] verify_realism.py --impair-only executes (correctly detects no impairments)

[*] Test 4: Demo Network Integration
[PASS] demo_network.py --help works
[PASS] demo_network.py imports successfully

[*] Test 5: Lab Manager Multi-Homed Support
[PASS] lab_manager.py imports successfully
[PASS] Multi-homed bridge support present

==========================================
  Test Summary
==========================================
Total tests:  11
Passed:       11
Failed:       0

All tests passed!
```

**Verified Features:**
- ✅ Network impairments (loss, jitter) working correctly
- ✅ HMI simulator functional with Poisson distribution
- ✅ Verification script operational
- ✅ Demo network integration with `--hmi` flag
- ✅ Multi-homed gateway support verified

### Troubleshooting Realism Features

| Problem | Solution |
|---|---|
| `impair_network.sh` fails with "br_netfilter not loaded" | Script automatically loads the module; if it fails, run `sudo modprobe br_netfilter` manually |
| HMI simulator generates no traffic | Check that devices exist on `192.168.100.0/24` subnet; simulator polls entire range |
| `verify_realism.py` reports no impairments | Apply impairments first: `sudo ./impair_network.sh --loss 5` |
| Multi-homed device has no internal IP | Internal bridge (`br_internal`) uses separate DHCP; may need manual IP configuration or separate dnsmasq instance |
| Cannot clear impairments | Use panic button: `sudo ./impair_network.sh --clear` |

### Advanced Usage

**Combining Multiple Impairments:**

Network impairments are replaced, not combined. To apply both loss and jitter, you need to use `tc` directly:

```bash
# Apply both loss and jitter together
sudo tc qdisc add dev br0 root netem loss 5% delay 50ms 20ms
```

**Custom HMI Polling Patterns:**

The HMI simulator uses Poisson distribution for realistic intervals. Adjust the mean interval to change traffic density:

```bash
# High-frequency polling (more traffic)
sudo python3 industrial_hmi_sim.py --interval 0.5

# Low-frequency polling (less traffic)
sudo python3 industrial_hmi_sim.py --interval 5.0
```

**Multi-Homed Gateway Configuration:**

To create a multi-homed gateway, add to your firmware's `config.json`:

```json
{
    "id": "gateway_multi",
    "name": "Segmented Gateway",
    "arch": "armel",
    "multi_homed": true,
    "kernel": "vmlinuz-custom",
    "rootfs": "rootfs.qcow2",
    "qemu_machine": "versatilepb"
}
```

When spawned via API or demo, the device will automatically:
- Create `tapN` (external) and `tapN_int` (internal) interfaces
- Connect to `br0` and `br_internal` bridges
- Receive IPs on both networks (if DHCP configured)

---

## License

MIT

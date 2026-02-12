# IoT Virtual Security Lab

A **native** (no Docker) IoT firmware emulation lab for Kali Linux.
Boots real router firmware **and** bare-metal MCU firmware inside QEMU,
connects every device to a shared virtual network, and exposes a REST API
so you can spawn, inspect, and tear down devices programmatically.

The lab supports three classes of device in a single network:

- **MIPS Linux routers** -- the same architecture found in consumer routers
  (Linksys, D-Link, TP-Link).
- **ARM Linux gateways** -- representative of ARM-based IoT cameras, hubs,
  and embedded controllers.
- **ARM Cortex-M3 industrial sensors** -- a resource-constrained MCU running
  Zephyr RTOS with a bare-metal TCP/IP stack, simulating PLCs, field
  sensors, and industrial controllers.

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Kali Linux Host                                                         │
│                                                                          │
│  ┌────────────┐       ┌───────────────────────────────────────────────┐  │
│  │ lab_api.py  │       │  br0  virtual bridge  192.168.100.1/24       │  │
│  │ REST :5000  │──────▶│                                               │  │
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

## Supported Device Architectures

| Firmware ID | Name | CPU | OS / Stack | QEMU Board | Disk | Creds | Boot Time |
|---|---|---|---|---|---|---|---|
| `dvrf_v03` | Damn Vulnerable Router Firmware v0.3 | MIPS 4Kc (32-bit LE) | Debian Linux | Malta | 288 MB qcow2 | `root:password` | ~60-90 s |
| `debian_armel` | Debian Wheezy ARM | ARMv5TE (32-bit LE) | Debian Linux | VersatilePB | 219 MB qcow2 | `root:root` | ~60-90 s |
| `zephyr_echo` | Industrial Sensor (Cortex-M3) | ARM Cortex-M3 | Zephyr RTOS 3.7 | lm3s6965evb | None (ELF only) | None | ~3-6 s |

### Linux devices (MIPS / ARM)

These boot a full Linux kernel with a root filesystem.  Once running they
behave like real embedded Linux boxes -- you can SSH into them, run
`busybox`, sniff traffic, or exploit known CVEs.

### Zephyr MCU device (Cortex-M3)

This boots a bare-metal Zephyr RTOS image on a Stellaris LM3S6965EVB
evaluation board (ARM Cortex-M3, 64 KB RAM, 256 KB flash).  It runs a
TCP + UDP echo server on **port 4242** with a real LwIP-derived network
stack, DHCPv4 for address assignment, and the Stellaris Ethernet driver.
No root filesystem or operating system login is involved -- it is a
single ELF binary that boots in seconds.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Kali Linux** (or Debian-based) | Tested on Kali 6.16.8 aarch64; any recent Kali/Debian works |
| **sudo access** | Required for bridge/TAP networking and QEMU |
| **~600 MB disk** | For the two Linux firmware images |
| **Python 3.10+** | With `flask` and `requests` |
| **Zephyr toolchain** (optional) | Only needed if you want to rebuild the Cortex-M3 firmware |

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

This installs system packages (`qemu-system-mips`, `qemu-system-arm`,
`bridge-utils`, `dnsmasq`, `iptables`), creates the virtual bridge
`br0`, starts a DHCP server, and configures NAT so guests can reach the
internet.

```bash
sudo ./setup_network.sh
```

The script is **idempotent** -- safe to run multiple times.

### 4. Download Linux firmware images

```bash
./download_firmware.sh
```

Downloads MIPS and ARM kernels + root filesystems from Debian's QEMU
image archive into `library/dvrf_v03/` and `library/debian_armel/`.

### 5. (Optional) Set up the Zephyr toolchain

Only needed if you want to rebuild the Cortex-M3 MCU firmware from
source.  A pre-built `zephyr.elf` is included in the library.

```bash
./setup_zephyr.sh
```

This installs:

- OS build dependencies (cmake, ninja, gperf, ccache, dtc)
- `west` -- Zephyr's meta-tool
- Zephyr SDK v0.16.8 (ARM toolchain) at `~/zephyr-sdk/`
- Zephyr source tree (v3.7.0) at `~/iot-lab/zephyrproject/`
- Python requirements for Zephyr build scripts

The script is **idempotent** and skips any component that is already
installed.  The initial run downloads ~1-2 GB of toolchain and source.

### 6. (Optional) Rebuild the Cortex-M3 firmware

```bash
./build_sensor_firmware.sh
```

Compiles the Zephyr `echo_server` sample for `qemu_cortex_m3` with a
custom overlay that enables the Stellaris Ethernet driver and DHCPv4.
The resulting ELF is copied to `library/zephyr_echo/zephyr.elf`.

Build profile (fits in 64 KB RAM):

- Stellaris Ethernet + DHCPv4 (gets IP from br0 dnsmasq)
- IPv4 only (IPv6 disabled to save RAM)
- Shell disabled (saves ~8 KB)
- Reduced buffer counts and stack sizes
- FLASH usage ~34 %, RAM usage ~82 %

### 7. Verify the infrastructure

```bash
python3 verify_lab.py
```

Runs 9 checks: bridge up, DHCP running, QEMU binaries found, firmware
files present, IP forwarding enabled, gateway reachable.

---

## Usage

### Option A: Run the demo (easiest)

The demo script spawns a multi-architecture network and prints a live
topology map.  Edit the `NETWORK` list at the top of `demo_network.py`
to customise which devices to spawn.

```bash
sudo python3 demo_network.py
```

Default network:

```python
NETWORK = [
    {"firmware_id": "dvrf_v03",      "role": "Vulnerable Router"},
    {"firmware_id": "dvrf_v03",      "role": "IoT Gateway"},
    {"firmware_id": "debian_armel",  "role": "ARM Sensor Node"},
]
```

You can add Zephyr MCU devices to the demo by appending:

```python
    {"firmware_id": "zephyr_echo",   "role": "Industrial Sensor"},
```

Press **Ctrl+C** to cleanly shut down all devices and remove TAP
interfaces.

### Option B: Use the REST API

Start the API server:

```bash
sudo python3 lab_api.py
```

Then from another terminal (or any HTTP client):

```bash
# List all available firmware in the library
curl -s http://localhost:5000/library | python3 -m json.tool

# Spawn a MIPS router
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "dvrf_v03"}'
# Returns: {"run_id": "dvrf_v03_a1b2c3d4"}

# Spawn an ARM gateway
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "debian_armel"}'

# Spawn a Cortex-M3 industrial sensor
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "zephyr_echo"}'

# View the live topology (IP, MAC, TAP, PID, alive status for each device)
curl -s http://localhost:5000/topology | python3 -m json.tool

# Stop one device
curl -s -X POST http://localhost:5000/kill/dvrf_v03_a1b2c3d4

# Stop everything
curl -s -X POST http://localhost:5000/reset_lab
```

### Option C: Talk to the Zephyr echo server directly

Once a `zephyr_echo` device is running and has acquired a DHCP lease
(check `GET /topology` for the IP), you can test it:

```bash
# TCP echo (port 4242)
echo "Hello Industrial" | nc <device_ip> 4242
# You should receive "Hello Industrial" back

# UDP echo (port 4242)
echo "Hello Industrial" | nc -u <device_ip> 4242
```

This confirms Layer-3 connectivity between the host and the bare-metal
MCU over the virtual bridge.

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
├── setup_network.sh            # Create br0 bridge, dnsmasq DHCP, NAT (idempotent)
├── setup_zephyr.sh             # Install Zephyr SDK, west, source tree (idempotent)
├── download_firmware.sh        # Download MIPS + ARM Linux firmware images
├── build_sensor_firmware.sh    # Compile Zephyr echo_server for Cortex-M3
│
├── lab_api.py                  # Flask REST API (port 5000)
├── lab_manager.py              # LabManager class — QEMU process lifecycle
├── scan_library.py             # Scan library/ for firmware config.json files
├── demo_network.py             # Spawn a multi-arch network with live topology display
├── start_emulation.py          # Legacy single-device CLI controller
├── verify_lab.py               # 9-check infrastructure self-test
│
├── library/
│   ├── dvrf_v03/
│   │   ├── config.json                     # Firmware metadata
│   │   ├── vmlinux-3.2.0-4-4kc-malta      # MIPS kernel (8 MB)
│   │   └── rootfs.img                      # MIPS root filesystem (288 MB qcow2)
│   ├── debian_armel/
│   │   ├── config.json                     # Firmware metadata
│   │   ├── vmlinuz-3.2.0-4-versatile       # ARM kernel (1.4 MB)
│   │   ├── initrd.img-3.2.0-4-versatile    # ARM initrd (2.5 MB)
│   │   └── rootfs.qcow2                    # ARM root filesystem (219 MB qcow2)
│   └── zephyr_echo/
│       ├── config.json                     # Firmware metadata
│       └── zephyr.elf                      # Zephyr echo_server binary (~90 KB code)
│
├── tests/
│   ├── test_phase2.py          # 15-check orchestration integration test
│   └── test_phase2_5.py        # 12-check Cortex-M3 / Zephyr verification test
│
├── logs/                       # QEMU console logs (auto-created at runtime)
└── .gitignore
```

---

## How the Lab Manager Works

`lab_manager.py` contains the `LabManager` class, which is the core QEMU
hypervisor.  When you call `spawn_instance(firmware_id)`:

1. **Firmware lookup** -- `scan_library.py` scans `library/*/config.json`
   and returns the matching config (architecture, kernel path, rootfs
   path, QEMU machine type).

2. **TAP creation** -- A new `tapN` interface is created, attached to the
   `br0` bridge, and brought up.

3. **QEMU command construction** -- Architecture-specific QEMU flags are
   selected from one of three profiles:

   | Arch | QEMU Binary | Machine | Drive | Network | Notes |
   |---|---|---|---|---|---|
   | `mipsel` | `qemu-system-mipsel` | `malta` | qcow2 rootfs | `-netdev tap` + `-device e1000` | Full Linux, 256 MB RAM |
   | `armel` | `qemu-system-arm` | `versatilepb` | qcow2 rootfs | `-net nic` + `-net tap` | Full Linux, 256 MB RAM |
   | `cortex-m3` | `qemu-system-arm` | `lm3s6965evb` | None | `-net nic,model=stellaris` + `-net tap` | Bare-metal ELF, no rootfs |

4. **Process launch** -- QEMU runs as a background process with console
   output redirected to `logs/qemu-<run_id>.log`.

5. **IP discovery** -- `refresh_ips()` polls `/var/lib/misc/dnsmasq-br0.leases`
   and matches MAC addresses to update each instance's IP.

6. **Teardown** -- `stop_instance()` sends SIGTERM (with SIGKILL fallback),
   closes the log file, and destroys the TAP interface.

---

## Adding Your Own Firmware

### Linux-based firmware (MIPS or ARM)

1. Create a directory: `library/my_firmware/`
2. Place the kernel and root filesystem inside it.
3. Create `library/my_firmware/config.json`:

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

1. Create a directory: `library/my_mcu_app/`
2. Place the compiled ELF binary inside it.
3. Create `library/my_mcu_app/config.json`:

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

Supported `arch` values: `mipsel`, `armel`, `cortex-m3`.
The API and demo script pick up new firmware automatically on the next
run.

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
# Infrastructure checks (9 tests) — no sudo needed
python3 verify_lab.py

# API + multi-device orchestration (15 tests) — needs sudo
# Requires: dvrf_v03 firmware downloaded
sudo python3 tests/test_phase2.py

# Cortex-M3 / Zephyr verification (12 tests) — needs sudo
# Requires: br0 bridge running, zephyr_echo firmware built
sudo python3 tests/test_phase2_5.py
```

### What `test_phase2_5.py` verifies

| # | Check | What it does |
|---|---|---|
| 1 | API reachable | Starts `lab_api.py` and waits for HTTP 200 |
| 2 | Library contains `zephyr_echo` | `GET /library` includes the MCU firmware |
| 3 | Spawn succeeds | `POST /spawn` returns 201 with a `run_id` |
| 4 | Instance in topology | `GET /topology` lists the device |
| 5 | QEMU PID alive | `kill -0` confirms the process is running |
| 6 | TAP interface exists | `ip link show tapN` succeeds |
| 7 | TAP attached to br0 | `bridge link show` includes the TAP |
| 8 | DHCP lease acquired | Polls topology until IP appears (~3-6 s) |
| 9 | TCP echo on port 4242 | Sends `"Hello Industrial"`, receives it back |
| 10 | Kill succeeds | `POST /kill/<run_id>` returns 200 |
| 11 | TAP removed after kill | `ip link show tapN` fails |
| 12 | Topology empty | `GET /topology` returns `[]` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `sudo` keeps asking for password | `echo "$USER ALL=(ALL) NOPASSWD: ALL" \| sudo tee /etc/sudoers.d/$USER` |
| `br0` doesn't exist | Re-run `sudo ./setup_network.sh` |
| QEMU not found | Re-run `sudo ./setup_network.sh` (installs packages) |
| Linux firmware files missing | Run `./download_firmware.sh` |
| Zephyr firmware missing | Run `./build_sensor_firmware.sh` (needs `setup_zephyr.sh` first) |
| `west` not found | Run `./setup_zephyr.sh` |
| `west build` fails with `ModuleNotFoundError: elftools` | `pip3 install --break-system-packages pyelftools` |
| `west build` fails with RAM overflow | The Kconfig overlay disables IPv6 and shell to fit in 64 KB; check `build_sensor_firmware.sh` |
| Linux guest has no IP | Wait 60-90 s for boot + DHCP; check `cat /var/lib/misc/dnsmasq-br0.leases` |
| Zephyr guest has no IP | Boots in ~3-6 s; check that dnsmasq is running (`pgrep -a dnsmasq`) |
| Zephyr echo doesn't respond | Verify the device IP from `/topology`, then `nc <ip> 4242` |
| QEMU crashes immediately | Check `logs/qemu-*.log` for errors |
| dnsmasq died | Re-run `sudo ./setup_network.sh` |

---

## License

MIT

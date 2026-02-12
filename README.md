# IoT Virtual Security Lab

A **native** (no Docker) IoT firmware emulation lab for Kali Linux.  
Boots real router and embedded-device firmware inside QEMU virtual machines, connects them to a shared virtual network, and exposes a REST API so you can spawn, inspect, and kill devices programmatically.

---

## What does this project do?

It lets you build a **virtual network of IoT devices** on a single machine:

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Kali Linux host                                           │
│                                                                 │
│  ┌───────────┐       ┌────────────────────────────────────────┐ │
│  │ lab_api.py │       │  br0  virtual bridge  192.168.100.1   │ │
│  │ REST :5000 │──────▶│                                        │ │
│  └───────────┘       │   tap0 ── QEMU VM 1 (MIPS router)     │ │
│        │              │   tap1 ── QEMU VM 2 (MIPS gateway)    │ │
│        ▼              │   tap2 ── QEMU VM 3 (ARM sensor)      │ │
│  ┌───────────┐       │                                        │ │
│  │lab_manager│       │   dnsmasq ── DHCP for guests           │ │
│  │   .py     │       │   iptables ─ NAT to internet           │ │
│  └───────────┘       └────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

Each virtual machine runs on its own QEMU process with a dedicated TAP
network interface.  All devices sit on the same Layer-2 bridge and get
IP addresses from a local DHCP server — just like physical devices on a
real switch.

---

## Supported Device Architectures

The lab ships with two pre-configured firmware images.  You can add more
by dropping files into the `library/` folder (see below).

| Firmware ID | Name | CPU | Bit-width | Endianness | QEMU Board | Disk | Default Creds |
|-------------|------|-----|-----------|------------|------------|------|---------------|
| `dvrf_v03` | Damn Vulnerable Router Firmware v0.3 | MIPS 4Kc | 32-bit | Little-endian | Malta | 288 MB qcow2 | `root:password` |
| `debian_armel` | Debian Wheezy ARM | ARMv5TE | 32-bit | Little-endian | VersatilePB | 219 MB qcow2 | `root:root` |

**MIPS Malta** emulates a classic MIPS development board — the same
architecture found in many consumer routers (Linksys, D-Link, TP-Link).

**ARM VersatilePB** emulates an ARM9 evaluation board — representative
of ARM-based IoT sensors, cameras, and embedded controllers.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Kali Linux** | Tested on 6.16.8 aarch64; any recent Kali/Debian works |
| **sudo access** | Required for bridge/TAP networking and QEMU |
| **~600 MB disk** | For the two firmware images |
| **Python 3.10+** | With `flask` and `requests` |

---

## Installation (step by step)

### 1. Clone the repository

```bash
git clone https://github.com/Adelsamir01/iot_vlab.git
cd iot_vlab
```

### 2. Install Python dependencies

```bash
pip3 install flask requests
# On Kali you may need: pip3 install --break-system-packages flask requests
```

### 3. Run the host setup script

This installs system packages (`qemu-system-mips`, `qemu-system-arm`,
`binwalk`, `bridge-utils`, `dnsmasq`, `iptables`), creates the virtual
bridge `br0`, starts the DHCP server, and configures NAT.

```bash
sudo ./setup_network.sh
```

The script is **idempotent** — safe to run multiple times.

### 4. Download firmware images

```bash
./download_firmware.sh
```

This fetches the MIPS and ARM kernels + root filesystems into
`library/dvrf_v03/` and `library/debian_armel/`.

### 5. Verify the infrastructure

```bash
python3 verify_lab.py
```

You should see 9/9 checks pass (bridge up, DHCP running, QEMU binaries
found, firmware files present, etc.).

---

## Usage

### Option A: Run the demo (easiest)

The demo script spawns a multi-architecture network and prints a live
topology map:

```bash
sudo python3 demo_network.py
```

You will see output like:

```
========================================================================
  IoT Cyber Range — Multi-Architecture Network Demo
========================================================================

  Available Firmware Library
  ----------------------------------------------------
  [dvrf_v03]
    Name : Damn Vulnerable Router Firmware v0.3
    Arch : MIPS32 Little-Endian
    Board: malta
    Creds: root:password
  [debian_armel]
    Name : Debian Wheezy ARM (VersatilePB)
    Arch : ARMv5 Little-Endian
    Board: versatilepb
    Creds: root:root

  Spawning devices...

  [+] Spawned: Vulnerable Router       (dvrf_v03,    run_id=dvrf_v03_a1b2c3d4)
  [+] Spawned: IoT Gateway             (dvrf_v03,    run_id=dvrf_v03_e5f6a7b8)
  [+] Spawned: ARM Sensor Node         (debian_armel, run_id=debian_armel_c9d0e1f2)

  Device Summary
  ----------------------------------------------------------------------------------------------------
  Role                 Arch                   TAP    MAC                IP                PID      State
  ----------------------------------------------------------------------------------------------------
  Vulnerable Router    MIPS32 Little-Endian   tap0   52:54:00:a1:b2:c3  192.168.100.10    12345    UP
  IoT Gateway          MIPS32 Little-Endian   tap1   52:54:00:d4:e5:f6  192.168.100.11    12346    UP
  ARM Sensor Node      ARMv5 Little-Endian    tap2   52:54:00:78:9a:bc  192.168.100.12    12347    UP
  ----------------------------------------------------------------------------------------------------
  Total: 3 device(s)

  Press Ctrl+C to tear down the network and exit.
```

Press **Ctrl+C** to cleanly shut down all devices and remove TAP
interfaces.

#### Customising the demo network

Edit the `NETWORK` list at the top of `demo_network.py`:

```python
NETWORK = [
    {"firmware_id": "dvrf_v03",      "role": "Vulnerable Router"},
    {"firmware_id": "dvrf_v03",      "role": "IoT Gateway"},
    {"firmware_id": "debian_armel",  "role": "ARM Sensor Node"},
]
```

Add or remove entries to change how many devices spawn and which
firmware each one runs.  Every `firmware_id` must match a directory
in `library/`.

### Option B: Use the REST API

Start the API server:

```bash
sudo python3 lab_api.py
```

Then from another terminal (or any HTTP client):

```bash
# List available firmware
curl -s http://localhost:5000/library | python3 -m json.tool

# Spawn a MIPS device
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "dvrf_v03"}'
# → {"run_id": "dvrf_v03_a1b2c3d4"}

# Spawn an ARM device
curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id": "debian_armel"}'
# → {"run_id": "debian_armel_e5f6a7b8"}

# View the live topology
curl -s http://localhost:5000/topology | python3 -m json.tool

# Stop one device
curl -s -X POST http://localhost:5000/kill/dvrf_v03_a1b2c3d4

# Stop everything
curl -s -X POST http://localhost:5000/reset_lab
```

#### API reference

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `GET` | `/library` | — | List all firmware in the library |
| `GET` | `/topology` | — | List all running VMs with IP, MAC, TAP, PID |
| `POST` | `/spawn` | `{"firmware_id": "..."}` | Boot a new VM (returns `run_id`) |
| `POST` | `/kill/<run_id>` | — | Stop and clean up one VM |
| `POST` | `/reset_lab` | — | Kill all VMs |

---

## Project Structure

```
.
├── setup_network.sh          # Install packages, create br0, start dnsmasq, configure NAT
├── download_firmware.sh      # Download MIPS + ARM firmware into library/
├── demo_network.py           # Spawn a multi-arch network and print topology
├── lab_api.py                # Flask REST API (port 5000)
├── lab_manager.py            # LabManager class — QEMU process lifecycle
├── scan_library.py           # Scan library/ for firmware configs
├── verify_lab.py             # 9-check infrastructure self-test
├── start_emulation.py        # Legacy single-device controller
├── library/
│   ├── dvrf_v03/
│   │   ├── config.json       # Firmware metadata
│   │   ├── vmlinux-3.2.0-4-4kc-malta   # MIPS kernel (8 MB)
│   │   └── rootfs.img        # MIPS root filesystem (288 MB)
│   └── debian_armel/
│       ├── config.json       # Firmware metadata
│       ├── vmlinuz-3.2.0-4-versatile    # ARM kernel (1.4 MB)
│       ├── initrd.img-3.2.0-4-versatile # ARM initrd (2.5 MB)
│       └── rootfs.qcow2      # ARM root filesystem (219 MB)
├── tests/
│   └── test_phase2.py        # 15-check integration test
├── logs/                     # QEMU console logs (auto-created)
└── .gitignore
```

---

## Adding Your Own Firmware

1. Create a directory: `library/my_firmware/`
2. Place the kernel and root filesystem files inside it.
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

Supported `arch` values: `mipsel`, `armel`.  
The API and demo script will pick it up automatically on the next run.

---

## Network Details

| Property | Value |
|----------|-------|
| Bridge interface | `br0` |
| Host IP (gateway) | `192.168.100.1` |
| DHCP range | `192.168.100.10` – `192.168.100.50` |
| Lease time | 12 hours |
| DNS servers | `8.8.8.8`, `8.8.4.4` |
| NAT | MASQUERADE via host's default interface |
| Max simultaneous VMs | ~40 (limited by DHCP range) |

---

## Running Tests

```bash
# Infrastructure checks (9 tests) — no sudo needed
python3 verify_lab.py

# API + multi-device orchestration (15 tests) — needs sudo
sudo python3 tests/test_phase2.py
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `sudo` keeps asking for password | Run: `echo "$USER ALL=(ALL) NOPASSWD: ALL" \| sudo tee /etc/sudoers.d/$USER` |
| `br0` doesn't exist | Re-run `sudo ./setup_network.sh` |
| QEMU not found | Re-run `sudo ./setup_network.sh` (installs packages) |
| Firmware files missing | Run `./download_firmware.sh` |
| Guest has no IP | Wait 60-90s for boot + DHCP; check `cat /var/lib/misc/dnsmasq-br0.leases` |
| ARM VM dies immediately | Check `logs/qemu-*.log` for errors |

---

## License

MIT

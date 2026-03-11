# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IoT Virtual Security Lab — a **native** (no Docker) IoT firmware emulation lab for Kali Linux. Boots real router and bare-metal MCU firmware inside QEMU, connects devices to virtual bridges, and exposes a REST API for programmatic control. Intended for security research and APIOT (autonomous security agent) integration.

## Commands

### Setup (one-time, requires sudo)
```bash
sudo ./setup_network.sh          # Create br0 bridge, start dnsmasq, enable NAT
./download_firmware.sh           # Download MIPS + ARM Linux firmware (~600 MB)
pip3 install flask requests      # Python dependencies
```

### Running the Lab
```bash
sudo python3 interactive_lab.py  # Interactive wizard + web dashboard at http://localhost:5000
sudo python3 lab_api.py          # REST API only at http://localhost:5000
sudo python3 demo_network.py     # Demo multi-arch network (edit NETWORK list in file)
```

### Testing
```bash
python3 verify_lab.py                    # Infrastructure self-test (no sudo needed)
sudo python3 tests/test_phase2.py        # Multi-device orchestration (needs running lab)
sudo python3 tests/test_phase2_5.py      # Cortex-M3 echo: 12 tests
sudo python3 tests/test_phase2_6.py      # CoAP + Modbus: 23 tests
bash test_realism_features.sh            # Network impairments + HMI: 11 tests
```

### Building Firmware (requires Zephyr SDK via `./setup_zephyr.sh`)
```bash
./build_sensor_firmware.sh    # Zephyr echo server → library/zephyr_echo/zephyr.elf
./build_advanced_firmware.sh  # CoAP + Modbus → library/zephyr_coap/ and library/arm_modbus_sim/
```

### Network Impairment
```bash
sudo bash impair_network.sh add br0 latency 50ms loss 1%   # Apply impairments
sudo bash impair_network.sh remove br0                      # Remove impairments
```

## Architecture

### Entry Points
| File | Purpose |
|------|---------|
| `interactive_lab.py` | CLI wizard + Flask server with Vis.js topology dashboard and SSE log streaming |
| `lab_api.py` | Minimal Flask REST API (port 5000) — delegates to `LabManager` |
| `lab_manager.py` | Core hypervisor: QEMU process lifecycle, TAP interface management, IP discovery |
| `demo_network.py` | Standalone multi-arch demo script |

### Network Topology
```
Host (Kali Linux)
  ├─ br0 (192.168.100.0/24)          # External/DMZ — all VMs default here
  │   ├─ tap0..N → QEMU processes
  │   └─ dnsmasq DHCP (.10-.50)
  ├─ br_internal (192.168.200.0/24)  # Isolated OT/manufacturing zone (Purdue model)
  │   ├─ tap*_int → multi-homed device second interface
  │   └─ dnsmasq DHCP (.10-.50)
  └─ NAT → host default interface → internet
```

### QEMU Architecture Profiles
| Arch | QEMU binary | Board | RAM | Network card |
|------|-------------|-------|-----|--------------|
| `mipsel` | `qemu-system-mipsel` | malta | 256 MB | e1000 |
| `armel` | `qemu-system-arm` | versatilepb | 256 MB | rtl8139 |
| `cortex-m3` | `qemu-system-arm` | lm3s6965evb | 64 KB | Stellaris (fixed MAC) |
| `riscv32` | `qemu-system-riscv32` | virt | 256 MB | virtio-net (placeholder, no Zephyr driver) |

### Firmware Library
Each entry under `library/*/config.json` defines a firmware profile. Fields: `id`, `name`, `arch`, `kernel`, `rootfs`, `qemu_machine`, `default_creds`. Multi-homed gateways add `"multi_homed": true`. The `scan_library.py` module reads these configs at startup.

### Firmware IDs and Protocols
| ID | Boot Time | Ports |
|----|-----------|-------|
| `dvrf_v03` | 60-90 s | SSH, HTTP |
| `debian_armel` | 60-90 s | SSH, HTTP |
| `zephyr_echo` | 3-6 s | TCP+UDP :4242 |
| `zephyr_coap` | 5-8 s | UDP :5683 |
| `arm_modbus_sim` | 5-8 s | TCP :502 |

### REST API Endpoints (port 5000)
```
GET  /library          # Available firmware profiles
GET  /topology         # Running devices: run_id, ip, mac, pid
POST /spawn            # Body: {"firmware_id": "...", "tags": [...]}
POST /kill/<run_id>    # Stop a specific device
POST /reset_lab        # Stop all devices
```

### Key Constraints
- **Cortex-M3 MAC**: Stellaris lm3s6965evb has a hardcoded MAC (`00:00:94:00:83:00`). Only **one** Cortex-M3 device can run simultaneously — `LabManager` enforces this.
- **Multi-homed support**: Only `mipsel` and `armel` support dual interfaces (both bridges).
- **DHCP range**: Max ~40 simultaneous VMs per bridge (range `.10-.50`).
- **All main scripts require `sudo`** (TAP/bridge management). Exception: `verify_lab.py`.

### APIOT Integration
APIOT is an autonomous security agent that queries `/topology` and `/library` for read-only reconnaissance, then operates entirely at L2/L3 directly on the bridge interfaces. It does **not** use the REST API for attack operations. See `docs/apiot-integration.md` and `docs/apiot-contract.md` for the full contract.

### Runtime Artifacts (auto-created, gitignored)
- `logs/qemu-<run_id>.log` — per-device QEMU console output
- `overlays/` — qcow2 copy-on-write disk overlays (preserves base firmware)

# llm_iot_sec

A native (non-Docker) IoT firmware emulation lab built on Kali Linux. Uses QEMU to boot vulnerable router firmware with full host network connectivity for security research and penetration testing. Includes a REST API for programmatic multi-device orchestration.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Kali Linux Host                                         │
│                                                          │
│  ┌────────────┐    ┌──────────────────────────────────┐  │
│  │  lab_api.py │    │  br0 (192.168.100.1/24)          │  │
│  │  Flask:5000 │───▶│   ├─ tap0 ──▶ QEMU VM #1 (MIPS) │  │
│  └────────────┘    │   ├─ tap1 ──▶ QEMU VM #2 (MIPS) │  │
│        │           │   ├─ dnsmasq (DHCP .10–.50)      │  │
│        ▼           │   └─ NAT ──▶ eth0/wlan           │  │
│  ┌────────────┐    └──────────────────────────────────┘  │
│  │lab_manager │                                          │
│  │  .py       │  Spawns/kills QEMU via subprocess        │
│  └────────────┘  Allocates TAPs & MACs dynamically       │
└──────────────────────────────────────────────────────────┘
```

## Prerequisites

- Kali Linux (tested on 6.16.8, aarch64)
- `sudo` access
- Python 3 with `flask` and `requests`

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Adelsamir01/llm_iot_sec.git
cd llm_iot_sec

# 2. Install Python deps
pip3 install flask requests

# 3. Set up host network & install system dependencies (requires sudo)
sudo ./setup_network.sh

# 4. Download firmware into the library
./download_dvrf.sh

# 5. Verify Phase 1 infrastructure
python3 verify_lab.py

# 6. Start the REST API (Phase 2)
sudo python3 lab_api.py

# 7. In another terminal — spawn a device
curl -X POST http://localhost:5000/spawn -H 'Content-Type: application/json' -d '{"firmware_id":"dvrf_v03"}'
```

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/library` | List available firmware in the library |
| `GET` | `/topology` | List all running QEMU instances |
| `POST` | `/spawn` | Boot a device `{"firmware_id": "dvrf_v03"}` |
| `POST` | `/kill/<run_id>` | Stop a specific device |
| `POST` | `/reset_lab` | Kill all running devices |

### Example: spawn + check + kill

```bash
# Spawn
RUN_ID=$(curl -s -X POST http://localhost:5000/spawn \
  -H 'Content-Type: application/json' \
  -d '{"firmware_id":"dvrf_v03"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

# Check topology
curl -s http://localhost:5000/topology | python3 -m json.tool

# Kill
curl -s -X POST http://localhost:5000/kill/$RUN_ID
```

## Project Structure

```
.
├── setup_network.sh          # Phase 1: idempotent host provisioning
├── download_dvrf.sh          # Phase 1: firmware downloader
├── start_emulation.py        # Phase 1: single-device QEMU controller (legacy)
├── verify_lab.py             # Phase 1: 9-check infrastructure self-test
├── scan_library.py           # Phase 2: firmware library scanner
├── lab_manager.py            # Phase 2: multi-device QEMU hypervisor (LabManager)
├── lab_api.py                # Phase 2: Flask REST API
├── library/
│   └── dvrf_v03/
│       └── config.json       # Firmware metadata (arch, kernel, rootfs, creds)
├── tests/
│   └── test_phase2.py        # Phase 2: 15-check integration test
└── .gitignore
```

## Firmware Library

Firmware lives in `library/<firmware_id>/` with a `config.json`:

```json
{
    "id": "dvrf_v03",
    "name": "Damn Vulnerable Router Firmware v0.3",
    "arch": "mipsel",
    "kernel": "vmlinux-3.2.0-4-4kc-malta",
    "rootfs": "rootfs.img",
    "qemu_machine": "malta",
    "default_creds": "root:password"
}
```

To add new firmware: create a directory under `library/`, place the kernel + rootfs inside, and write a `config.json`. The API picks it up automatically.

## Network Details

| Property | Value |
|----------|-------|
| Bridge | `br0` |
| Host IP | `192.168.100.1` |
| DHCP Range | `192.168.100.10` – `192.168.100.50` |
| Lease Time | 12 hours |
| DNS | `8.8.8.8`, `8.8.4.4` |
| NAT | MASQUERADE via host default interface |

## Tests

```bash
# Phase 1 — infrastructure checks (9 tests)
python3 verify_lab.py

# Phase 2 — API + multi-device orchestration (15 tests)
sudo python3 tests/test_phase2.py
```

## License

MIT

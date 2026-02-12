# llm_iot_sec

A native (non-Docker) IoT firmware emulation lab built on Kali Linux. Uses QEMU to boot vulnerable router firmware with full host network connectivity for security research and penetration testing.

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Kali Linux Host                                 │
│                                                  │
│  ┌──────────────┐     ┌───────────────────────┐  │
│  │ start_       │     │ br0 (192.168.100.1)   │  │
│  │ emulation.py │────▶│  ├─ tap0 ──▶ QEMU VM  │  │
│  └──────────────┘     │  ├─ dnsmasq (DHCP)    │  │
│                       │  └─ NAT ──▶ eth0/wlan │  │
│                       └───────────────────────┘  │
└──────────────────────────────────────────────────┘
```

- **Bridge `br0`** — Virtual switch at `192.168.100.1/24`
- **dnsmasq** — DHCP server (range `.10`–`.50`) on the bridge
- **iptables** — NAT/MASQUERADE so guests can reach the internet
- **QEMU** — Boots MIPS/ARM firmware headlessly with TAP networking

## Prerequisites

- Kali Linux (tested on 6.16.8, aarch64)
- `sudo` access

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Adelsamir01/llm_iot_sec.git
cd llm_iot_sec

# 2. Set up host network & install dependencies (requires sudo)
sudo ./setup_network.sh

# 3. Download firmware (MIPS Malta kernel + Debian Wheezy rootfs)
./download_dvrf.sh

# 4. Verify everything is ready
python3 verify_lab.py

# 5. Boot the emulated device
sudo python3 start_emulation.py
```

## Project Structure

```
.
├── setup_network.sh      # Installs packages, creates br0 bridge, dnsmasq, NAT rules
├── download_dvrf.sh      # Downloads MIPS LE kernel & rootfs into ~/iot-lab/firmware/
├── start_emulation.py    # QEMU controller (QemuInstance class) with TAP networking
├── verify_lab.py         # 9-check self-test for the infrastructure
└── .gitignore
```

## Scripts

### `setup_network.sh`

Idempotent provisioning script (safe to run multiple times). Installs:

| Package | Purpose |
|---------|---------|
| `qemu-system-mips` | MIPS emulation |
| `qemu-system-arm` | ARM emulation |
| `binwalk` | Firmware analysis |
| `bridge-utils` | Virtual bridge management |
| `dnsmasq` | DHCP/DNS for guests |
| `iptables` | NAT/firewall |

Then configures:
- Bridge `br0` at `192.168.100.1/24`
- dnsmasq DHCP range `192.168.100.10`–`192.168.100.50`
- IP forwarding (`sysctl`)
- MASQUERADE rule for internet access via the host's default interface

### `download_dvrf.sh`

Downloads into `~/iot-lab/firmware/`:
- **Kernel**: `vmlinux-3.2.0-4-4kc-malta` (Debian MIPS Malta, ~8MB)
- **Rootfs**: `rootfs.img` (Debian Wheezy MIPS LE qcow2, ~288MB)

### `start_emulation.py`

Python 3 QEMU hypervisor with a `QemuInstance` class:

```bash
# Default (MIPS, DVRF)
sudo python3 start_emulation.py

# Custom options
sudo python3 start_emulation.py --arch mips --tap tap0 --timeout 120
```

**Flags**: `--arch` (mips/arm), `--kernel`, `--rootfs`, `--tap`, `--mac`, `--timeout`

The controller:
1. Creates a TAP interface and attaches it to `br0`
2. Launches QEMU in headless mode (`-nographic`)
3. Monitors dnsmasq leases until the guest acquires an IP
4. Reports the guest IP and keeps running until `Ctrl+C`
5. Cleans up TAP interface on exit

### `verify_lab.py`

Runs 9 infrastructure checks:

1. `br0` exists and is UP
2. `br0` has IP `192.168.100.1/24`
3. dnsmasq is running on `br0`
4. `qemu-system-mipsel` is executable
5. `qemu-system-arm` is executable
6. Gateway `192.168.100.1` responds to ping
7. Kernel image is present
8. Rootfs image is present
9. IP forwarding is enabled

## Network Details

| Property | Value |
|----------|-------|
| Bridge | `br0` |
| Host IP | `192.168.100.1` |
| DHCP Range | `192.168.100.10` – `192.168.100.50` |
| Lease Time | 12 hours |
| DNS | `8.8.8.8`, `8.8.4.4` |
| NAT | MASQUERADE via host default interface |

## License

MIT

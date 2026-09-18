#!/usr/bin/env bash
# build_sensor_firmware.sh — Build Zephyr echo_server for qemu_cortex_m3 (Stellaris)
# Produces: library/zephyr_echo/zephyr.elf
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ZEPHYR_BASE="$HOME/iot-lab/zephyrproject/zephyr"
SAMPLE="samples/net/sockets/echo_server"
BOARD="qemu_cortex_m3"
BUILD_DIR="$ZEPHYR_BASE/build_echo_cortex_m3"
DEST_DIR="$SCRIPT_DIR/library/zephyr_echo"

info() { echo "[+] $*"; }
err()  { echo "[ERROR] $*" >&2; }

# ── Preflight checks ────────────────────────────────────────────────────
[[ -d "$ZEPHYR_BASE" ]] || { err "Zephyr source not found at $ZEPHYR_BASE. Run setup_zephyr.sh first."; exit 1; }
command -v west &>/dev/null || { err "west not found. Run setup_zephyr.sh first."; exit 1; }

# ── Create DHCP overlay ─────────────────────────────────────────────────
# The stock overlay-qemu_cortex_m3_eth.conf enables Ethernet but uses
# static IPs.  We layer on DHCP so the device gets an address from br0's
# dnsmasq, matching the existing lab infrastructure.
OVERLAY_DIR="$ZEPHYR_BASE/$SAMPLE"
OVERLAY_DHCP="$OVERLAY_DIR/overlay-dhcp.conf"

cat > "$OVERLAY_DHCP" <<'EOF'
# ── Stellaris Ethernet (QEMU lm3s6965evb) ──
CONFIG_NET_L2_ETHERNET=y
CONFIG_NET_QEMU_ETHERNET=y
CONFIG_NET_SLIP_TAP=n
CONFIG_SLIP=n

# ── DHCPv4 — get IP from br0 dnsmasq ──
CONFIG_NET_DHCPV4=y
CONFIG_NET_CONFIG_MY_IPV4_ADDR=""
CONFIG_NET_CONFIG_PEER_IPV4_ADDR=""

# ── RAM budget: qemu_cortex_m3 = 64 KB ──
# Disable IPv6 (saves ~10 KB)
CONFIG_NET_IPV6=n
CONFIG_NET_CONFIG_NEED_IPV6=n

# Disable interactive shell (saves ~8 KB)
CONFIG_SHELL=n
CONFIG_NET_SHELL=n
CONFIG_KERNEL_SHELL=n

# Shrink stacks and buffers
CONFIG_MAIN_STACK_SIZE=1536
CONFIG_NET_PKT_RX_COUNT=8
CONFIG_NET_PKT_TX_COUNT=8
CONFIG_NET_BUF_RX_COUNT=16
CONFIG_NET_BUF_TX_COUNT=16
CONFIG_NET_MAX_CONTEXTS=4
CONFIG_NET_IF_UNICAST_IPV4_ADDR_COUNT=1

# Minimal logging (keeps printk for boot banner)
CONFIG_NET_LOG=n
CONFIG_LOG=y
CONFIG_LOG_DEFAULT_LEVEL=3
EOF
info "Wrote DHCP overlay to $OVERLAY_DHCP"

# ── Build ────────────────────────────────────────────────────────────────
info "Building $SAMPLE for $BOARD ..."
cd "$ZEPHYR_BASE"

west build -p always -b "$BOARD" "$SAMPLE" \
    -d "$BUILD_DIR" \
    -- -DOVERLAY_CONFIG="overlay-dhcp.conf"

ELF="$BUILD_DIR/zephyr/zephyr.elf"
if [[ ! -f "$ELF" ]]; then
    err "Build succeeded but zephyr.elf not found at $ELF"
    exit 1
fi

info "Build OK: $ELF ($(du -h "$ELF" | cut -f1))"

# ── Install into firmware library ────────────────────────────────────────
mkdir -p "$DEST_DIR"
cp "$ELF" "$DEST_DIR/zephyr.elf"
info "Installed to $DEST_DIR/zephyr.elf"

echo ""
echo "========================================="
echo "  Sensor firmware ready"
echo "  Board:    $BOARD (lm3s6965evb)"
echo "  Binary:   $DEST_DIR/zephyr.elf"
echo "  App:      echo_server (TCP+UDP :4242)"
echo "========================================="

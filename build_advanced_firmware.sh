#!/usr/bin/env bash
# build_advanced_firmware.sh — Build CoAP server + Fake PLC for qemu_cortex_m3
# Produces:
#   library/zephyr_coap/zephyr.elf      (CoAP server, UDP :5683)
#   library/arm_modbus_sim/zephyr.elf   (TCP echo on :502, "Fake PLC")
#
# NOTE: qemu_riscv32 lacks an Ethernet driver in Zephyr 3.7 (no virtio-net
# binding, no PCI+e1000 DTS, SLIP requires unavailable second UART).
# Both firmware therefore target qemu_cortex_m3 (Stellaris Ethernet) which
# bridges to br0 reliably.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ZEPHYR_BASE="$HOME/iot-lab/zephyrproject/zephyr"
BOARD="qemu_cortex_m3"

info() { echo "[+] $*"; }
err()  { echo "[ERROR] $*" >&2; }

# ── Preflight ────────────────────────────────────────────────────────────
[[ -d "$ZEPHYR_BASE" ]] || { err "Zephyr not found at $ZEPHYR_BASE. Run setup_zephyr.sh first."; exit 1; }
command -v west &>/dev/null || { err "west not found. Run setup_zephyr.sh first."; exit 1; }

cd "$ZEPHYR_BASE"

# ═══════════════════════════════════════════════════════════════════════════
# Firmware A: CoAP Server (UDP :5683)
# ═══════════════════════════════════════════════════════════════════════════
COAP_SAMPLE="samples/net/sockets/coap_server"
COAP_BUILD="$ZEPHYR_BASE/build_coap_cortex_m3"
COAP_DEST="$SCRIPT_DIR/library/zephyr_coap"
COAP_OVERLAY="$ZEPHYR_BASE/$COAP_SAMPLE/overlay-cortex-m3-dhcp.conf"

cat > "$COAP_OVERLAY" <<'EOF'
# Stellaris Ethernet
CONFIG_NET_L2_ETHERNET=y
CONFIG_NET_QEMU_ETHERNET=y
CONFIG_NET_SLIP_TAP=n
CONFIG_SLIP=n

# IPv4 + DHCP
CONFIG_NET_IPV6=n
CONFIG_NET_CONFIG_NEED_IPV6=n
CONFIG_NET_IPV4=y
CONFIG_NET_CONFIG_NEED_IPV4=y
CONFIG_NET_DHCPV4=y
CONFIG_NET_CONFIG_MY_IPV4_ADDR=""
CONFIG_NET_CONFIG_PEER_IPV4_ADDR=""

# RAM savings
CONFIG_SHELL=n
CONFIG_NET_SHELL=n
CONFIG_KERNEL_SHELL=n
CONFIG_COAP_SERVER_SHELL=n
CONFIG_MAIN_STACK_SIZE=1536
CONFIG_NET_PKT_RX_COUNT=8
CONFIG_NET_PKT_TX_COUNT=8
CONFIG_NET_BUF_RX_COUNT=16
CONFIG_NET_BUF_TX_COUNT=16
CONFIG_NET_MAX_CONTEXTS=4

# Minimal logging
CONFIG_NET_LOG=n
CONFIG_LOG=y
CONFIG_LOG_DEFAULT_LEVEL=2
EOF

info "Building CoAP server for $BOARD ..."
west build -p always -b "$BOARD" "$COAP_SAMPLE" \
    -d "$COAP_BUILD" \
    -- -DOVERLAY_CONFIG="overlay-cortex-m3-dhcp.conf"

COAP_ELF="$COAP_BUILD/zephyr/zephyr.elf"
[[ -f "$COAP_ELF" ]] || { err "CoAP build OK but zephyr.elf missing at $COAP_ELF"; exit 1; }
info "CoAP build OK: $(du -h "$COAP_ELF" | cut -f1)"

mkdir -p "$COAP_DEST"
cp "$COAP_ELF" "$COAP_DEST/zephyr.elf"
info "Installed CoAP → $COAP_DEST/zephyr.elf"

# ═══════════════════════════════════════════════════════════════════════════
# Firmware B: Fake PLC — TCP echo on port 502
# ═══════════════════════════════════════════════════════════════════════════
PLC_APP="$HOME/iot-lab/zephyrproject/app_modbus"
PLC_BUILD="$ZEPHYR_BASE/build_plc_cortex_m3"
PLC_DEST="$SCRIPT_DIR/library/arm_modbus_sim"
PLC_OVERLAY="$PLC_APP/overlay-cortex-m3-dhcp.conf"

cat > "$PLC_OVERLAY" <<'EOF'
# Stellaris Ethernet
CONFIG_NET_L2_ETHERNET=y
CONFIG_NET_QEMU_ETHERNET=y
CONFIG_NET_SLIP_TAP=n
CONFIG_SLIP=n

# DHCP
CONFIG_NET_DHCPV4=y
CONFIG_NET_CONFIG_MY_IPV4_ADDR=""
CONFIG_NET_CONFIG_PEER_IPV4_ADDR=""

# RAM savings
CONFIG_SHELL=n
CONFIG_NET_SHELL=n
CONFIG_MAIN_STACK_SIZE=1536
CONFIG_NET_PKT_RX_COUNT=8
CONFIG_NET_PKT_TX_COUNT=8
CONFIG_NET_BUF_RX_COUNT=16
CONFIG_NET_BUF_TX_COUNT=16
CONFIG_NET_MAX_CONTEXTS=4

# Minimal logging
CONFIG_NET_LOG=n
CONFIG_LOG=y
CONFIG_LOG_DEFAULT_LEVEL=3
EOF

info "Building Fake PLC (TCP :502) for $BOARD ..."
west build -p always -b "$BOARD" "$PLC_APP" \
    -d "$PLC_BUILD" \
    -- -DOVERLAY_CONFIG="overlay-cortex-m3-dhcp.conf"

PLC_ELF="$PLC_BUILD/zephyr/zephyr.elf"
[[ -f "$PLC_ELF" ]] || { err "PLC build OK but zephyr.elf missing at $PLC_ELF"; exit 1; }
info "PLC build OK: $(du -h "$PLC_ELF" | cut -f1)"

mkdir -p "$PLC_DEST"
cp "$PLC_ELF" "$PLC_DEST/zephyr.elf"
info "Installed Fake PLC → $PLC_DEST/zephyr.elf"

# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo "========================================="
echo "  Phase 2.6 Firmware Ready"
echo "  Board:  $BOARD (lm3s6965evb)"
echo ""
echo "  A) CoAP Server    → $COAP_DEST/zephyr.elf"
echo "     Protocol: CoAP (UDP :5683)"
echo ""
echo "  B) Fake PLC       → $PLC_DEST/zephyr.elf"
echo "     Protocol: TCP echo on :502 (Modbus port)"
echo "========================================="

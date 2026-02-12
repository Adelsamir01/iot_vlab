#!/usr/bin/env bash
# download_dvrf.sh — Download DVRF v0.3 MIPS little-endian kernel & rootfs for QEMU
set -euo pipefail

FW_DIR="$HOME/iot-lab/library/dvrf_v03"
KERNEL_FILE="$FW_DIR/vmlinux-3.2.0-4-4kc-malta"
ROOTFS_FILE="$FW_DIR/rootfs.img"

# Debian MIPS Malta kernel (used by DVRF for QEMU emulation)
KERNEL_URL="https://people.debian.org/~aurel32/qemu/mipsel/vmlinux-3.2.0-4-4kc-malta"
ROOTFS_URL="https://people.debian.org/~aurel32/qemu/mipsel/debian_wheezy_mipsel_standard.qcow2"

info()  { echo "[+] $*"; }
warn()  { echo "[!] $*"; }

mkdir -p "$FW_DIR"

# Download kernel
if [[ -f "$KERNEL_FILE" ]]; then
    info "Kernel already present: $KERNEL_FILE"
else
    info "Downloading MIPS Malta kernel..."
    if wget -q --show-progress -O "$KERNEL_FILE" "$KERNEL_URL"; then
        info "Kernel saved to $KERNEL_FILE"
    else
        warn "Failed to download kernel from $KERNEL_URL"
        warn "Manual step: place a MIPS little-endian kernel at:"
        warn "  $KERNEL_FILE"
        exit 1
    fi
fi

# Download rootfs
if [[ -f "$ROOTFS_FILE" ]]; then
    info "Root filesystem already present: $ROOTFS_FILE"
else
    info "Downloading Debian Wheezy MIPS rootfs (used as DVRF base)..."
    if wget -q --show-progress -O "$ROOTFS_FILE" "$ROOTFS_URL"; then
        info "Rootfs saved to $ROOTFS_FILE"
    else
        warn "Failed to download rootfs from $ROOTFS_URL"
        warn "Manual step: place a MIPS LE qcow2/raw rootfs at:"
        warn "  $ROOTFS_FILE"
        exit 1
    fi
fi

echo ""
echo "Firmware files ready in $FW_DIR:"
ls -lh "$FW_DIR"/

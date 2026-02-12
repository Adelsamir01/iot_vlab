#!/usr/bin/env bash
# download_firmware.sh — Download all firmware images for the IoT lab.
# Idempotent: skips files that already exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/library"

info()  { echo "[+] $*"; }
warn()  { echo "[!] $*"; }
err()   { echo "[ERROR] $*" >&2; }

# ── MIPS Little-Endian (DVRF v0.3 / Malta) ──────────────────────────────
MIPS_DIR="$LIB_DIR/dvrf_v03"
MIPS_KERNEL="$MIPS_DIR/vmlinux-3.2.0-4-4kc-malta"
MIPS_ROOTFS="$MIPS_DIR/rootfs.img"
MIPS_KERNEL_URL="https://people.debian.org/~aurel32/qemu/mipsel/vmlinux-3.2.0-4-4kc-malta"
MIPS_ROOTFS_URL="https://people.debian.org/~aurel32/qemu/mipsel/debian_wheezy_mipsel_standard.qcow2"

# ── ARM Little-Endian (Debian Wheezy / VersatilePB) ─────────────────────
ARM_DIR="$LIB_DIR/debian_armel"
ARM_KERNEL="$ARM_DIR/vmlinuz-3.2.0-4-versatile"
ARM_INITRD="$ARM_DIR/initrd.img-3.2.0-4-versatile"
ARM_ROOTFS="$ARM_DIR/rootfs.qcow2"
ARM_KERNEL_URL="https://people.debian.org/~aurel32/qemu/armel/vmlinuz-3.2.0-4-versatile"
ARM_INITRD_URL="https://people.debian.org/~aurel32/qemu/armel/initrd.img-3.2.0-4-versatile"
ARM_ROOTFS_URL="https://people.debian.org/~aurel32/qemu/armel/debian_wheezy_armel_standard.qcow2"

download() {
    local dest="$1" url="$2" label="$3"
    if [[ -f "$dest" ]]; then
        info "$label already present: $(basename "$dest")"
        return 0
    fi
    info "Downloading $label ..."
    mkdir -p "$(dirname "$dest")"
    if wget -q --show-progress -O "$dest" "$url"; then
        info "Saved: $dest ($(du -h "$dest" | cut -f1))"
    else
        err "Failed to download $label from $url"
        rm -f "$dest"
        return 1
    fi
}

echo ""
echo "========================================"
echo "  IoT Lab — Firmware Downloader"
echo "========================================"
echo ""

info "=== MIPS Little-Endian (dvrf_v03) ==="
download "$MIPS_KERNEL" "$MIPS_KERNEL_URL" "MIPS Malta kernel"
download "$MIPS_ROOTFS" "$MIPS_ROOTFS_URL" "MIPS rootfs (qcow2)"

echo ""
info "=== ARM Little-Endian (debian_armel) ==="
download "$ARM_KERNEL" "$ARM_KERNEL_URL" "ARM Versatile kernel"
download "$ARM_INITRD" "$ARM_INITRD_URL" "ARM Versatile initrd"
download "$ARM_ROOTFS" "$ARM_ROOTFS_URL" "ARM rootfs (qcow2)"

echo ""
echo "========================================"
echo "  Firmware Library Contents"
echo "========================================"
for dir in "$LIB_DIR"/*/; do
    [[ -f "$dir/config.json" ]] || continue
    name=$(python3 -c "import json; print(json.load(open('${dir}config.json'))['name'])" 2>/dev/null || basename "$dir")
    echo ""
    echo "  $name"
    ls -lh "$dir" | grep -v "^total\|config.json" | awk '{print "    " $NF " (" $5 ")"}'
done
echo ""

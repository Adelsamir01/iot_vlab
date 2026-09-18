#!/usr/bin/env bash
# setup_zephyr.sh — Idempotent Zephyr RTOS build environment provisioning
# Installs: OS deps, west, Zephyr SDK (ARM toolchain), Zephyr source tree
set -euo pipefail

ZEPHYR_SDK_VERSION="0.16.8"
ZEPHYR_SDK_DIR="$HOME/zephyr-sdk"
ZEPHYR_WORKSPACE="$HOME/iot-lab/zephyrproject"

HOST_ARCH=$(uname -m)
case "$HOST_ARCH" in
    aarch64) SDK_ARCH="linux-aarch64" ;;
    x86_64)  SDK_ARCH="linux-x86_64"  ;;
    *)       echo "[ERROR] Unsupported host architecture: $HOST_ARCH" >&2; exit 1 ;;
esac

SDK_TARBALL="zephyr-sdk-${ZEPHYR_SDK_VERSION}_${SDK_ARCH}_minimal.tar.xz"
SDK_URL="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${ZEPHYR_SDK_VERSION}/${SDK_TARBALL}"
TC_TARBALL="toolchain_${SDK_ARCH}_arm-zephyr-eabi.tar.xz"
TC_URL="https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${ZEPHYR_SDK_VERSION}/${TC_TARBALL}"

info() { echo "[+] $*"; }
warn() { echo "[!] $*"; }

# ── 1. OS dependencies ──────────────────────────────────────────────────────
info "Checking OS dependencies..."
PACKAGES=(cmake ninja-build gperf python3-dev python3-pip ccache device-tree-compiler
          wget xz-utils python3-venv git)
MISSING=()
for pkg in "${PACKAGES[@]}"; do
    dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    info "Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
else
    info "All OS dependencies already installed."
fi

# ── 2. West (Zephyr meta-tool) ──────────────────────────────────────────────
if command -v west &>/dev/null; then
    info "west already installed: $(west --version)"
else
    info "Installing west..."
    pip3 install --break-system-packages west
fi

# ── 3. Zephyr SDK ───────────────────────────────────────────────────────────
if [[ -d "$ZEPHYR_SDK_DIR" && -f "$ZEPHYR_SDK_DIR/sdk_version" ]]; then
    info "Zephyr SDK already installed at $ZEPHYR_SDK_DIR ($(cat "$ZEPHYR_SDK_DIR/sdk_version"))"
else
    info "Downloading Zephyr SDK ${ZEPHYR_SDK_VERSION} minimal..."
    wget -q --show-progress -O "/tmp/$SDK_TARBALL" "$SDK_URL"

    info "Downloading ARM toolchain..."
    wget -q --show-progress -O "/tmp/$TC_TARBALL" "$TC_URL"

    info "Extracting SDK to $ZEPHYR_SDK_DIR ..."
    rm -rf "$ZEPHYR_SDK_DIR"
    tar xf "/tmp/$SDK_TARBALL" -C "$HOME"
    mv "$HOME/zephyr-sdk-${ZEPHYR_SDK_VERSION}" "$ZEPHYR_SDK_DIR"

    info "Extracting ARM toolchain into SDK..."
    tar xf "/tmp/$TC_TARBALL" -C "$ZEPHYR_SDK_DIR"

    info "Running SDK setup (registering CMake packages)..."
    cd "$ZEPHYR_SDK_DIR"
    yes | ./setup.sh

    rm -f "/tmp/$SDK_TARBALL" "/tmp/$TC_TARBALL"
    info "Zephyr SDK ${ZEPHYR_SDK_VERSION} installed."
fi

# ── 4. West workspace (Zephyr source) ───────────────────────────────────────
if [[ -d "$ZEPHYR_WORKSPACE/zephyr/.git" ]]; then
    info "Zephyr workspace already initialised at $ZEPHYR_WORKSPACE"
else
    info "Initialising west workspace at $ZEPHYR_WORKSPACE ..."
    mkdir -p "$(dirname "$ZEPHYR_WORKSPACE")"
    rm -rf "$ZEPHYR_WORKSPACE"
    west init "$ZEPHYR_WORKSPACE" --mr v3.7.0
    cd "$ZEPHYR_WORKSPACE"

    info "Running west update (fetching modules — may take several minutes)..."
    west update

    info "Exporting CMake packages..."
    west zephyr-export
fi

# ── 5. Python requirements ──────────────────────────────────────────────────
info "Installing Zephyr Python requirements..."
pip3 install --break-system-packages -r "$ZEPHYR_WORKSPACE/zephyr/scripts/requirements.txt"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "  Zephyr build environment ready"
echo "  SDK:       $ZEPHYR_SDK_DIR"
echo "  Workspace: $ZEPHYR_WORKSPACE"
echo "  west:      $(command -v west)"
echo "========================================="

#!/usr/bin/env bash
# impair_network.sh — Network Impairment Engine for Realistic Industrial Conditions
# Simulates "dirty" networking found in old industrial sites using tc (Traffic Control) and netem.
# Must be run with sudo.

set -euo pipefail

BRIDGE="br0"
BRIDGE_IP="192.168.100.1"

# ---------- helper functions ----------
info()  { echo "[+] $*"; }
warn()  { echo "[!] $*"; }
die()   { echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "This script must be run as root (use sudo)."

# Check if br_netfilter module is loaded (required for bridge traffic control)
check_br_netfilter() {
    if ! lsmod | grep -q "^br_netfilter"; then
        warn "br_netfilter module not loaded. Loading it..."
        modprobe br_netfilter || warn "Failed to load br_netfilter (may still work)"
    else
        info "br_netfilter module is loaded."
    fi
}

# Verify bridge exists
check_bridge() {
    if ! ip link show "$BRIDGE" &>/dev/null; then
        die "Bridge $BRIDGE does not exist. Run ./setup_network.sh first."
    fi
}

# Clear all impairments (panic button)
clear_impairments() {
    info "Clearing all network impairments on $BRIDGE..."
    
    # Remove all qdiscs (queuing disciplines) from the bridge
    tc qdisc del dev "$BRIDGE" root 2>/dev/null || true
    tc qdisc del dev "$BRIDGE" ingress 2>/dev/null || true
    
    info "Network impairments cleared. Bridge $BRIDGE is back to normal."
}

# Apply packet loss
apply_loss() {
    local loss_pct="$1"
    info "Applying ${loss_pct}% packet loss to $BRIDGE..."
    
    # Remove existing root qdisc if present
    tc qdisc del dev "$BRIDGE" root 2>/dev/null || true
    
    # Add netem qdisc with packet loss
    tc qdisc add dev "$BRIDGE" root netem loss "${loss_pct}%"
    
    info "Packet loss configured: ${loss_pct}%"
}

# Apply latency and jitter
apply_jitter() {
    local latency_ms="$1"
    local jitter_ms="$2"
    info "Applying ${latency_ms}ms latency with ${jitter_ms}ms jitter to $BRIDGE..."
    
    # Remove existing root qdisc if present
    tc qdisc del dev "$BRIDGE" root 2>/dev/null || true
    
    # Add netem qdisc with delay and jitter
    tc qdisc add dev "$BRIDGE" root netem delay "${latency_ms}ms" "${jitter_ms}ms"
    
    info "Latency/jitter configured: ${latency_ms}ms ± ${jitter_ms}ms"
}

# Show current impairments
show_status() {
    info "Current network impairments on $BRIDGE:"
    if tc qdisc show dev "$BRIDGE" | grep -q "netem"; then
        tc qdisc show dev "$BRIDGE"
    else
        echo "  No impairments active (clean network)"
    fi
}

# Main argument parsing
main() {
    check_bridge
    check_br_netfilter
    
    case "${1:-}" in
        --loss)
            if [[ -z "${2:-}" ]]; then
                die "Usage: $0 --loss PERCENTAGE (e.g., --loss 5)"
            fi
            apply_loss "$2"
            ;;
        --jitter)
            if [[ -z "${2:-}" ]] || [[ -z "${3:-}" ]]; then
                die "Usage: $0 --jitter LATENCY_MS JITTER_MS (e.g., --jitter 50 20)"
            fi
            apply_jitter "$2" "$3"
            ;;
        --clear)
            clear_impairments
            ;;
        --status)
            show_status
            ;;
        --help|-h)
            cat <<EOF
Network Impairment Engine for iot_vlab

Usage:
    $0 --loss PERCENTAGE          Apply packet loss (e.g., --loss 5)
    $0 --jitter LATENCY JITTER    Apply latency with jitter (e.g., --jitter 50 20)
    $0 --clear                    Clear all impairments (PANIC BUTTON)
    $0 --status                   Show current impairments

Examples:
    # Add 5% packet loss
    sudo $0 --loss 5

    # Add 50ms latency with 20ms jitter
    sudo $0 --jitter 50 20

    # Clear everything (panic button)
    sudo $0 --clear

    # Check current status
    sudo $0 --status

Safety:
    Always use --clear if you lock yourself out or need to reset the network.
    The script verifies br_netfilter module is loaded before applying changes.

EOF
            exit 0
            ;;
        "")
            warn "No arguments provided. Use --help for usage."
            show_status
            exit 1
            ;;
        *)
            die "Unknown option: $1. Use --help for usage."
            ;;
    esac
}

main "$@"

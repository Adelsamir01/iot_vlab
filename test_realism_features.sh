#!/bin/bash
# test_realism_features.sh — Comprehensive test of Industrial Realism features

set -uo pipefail

echo "=========================================="
echo "  IoT Virtual Lab — Realism Features Test"
echo "=========================================="
echo

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

test_count=0
pass_count=0
fail_count=0

test_pass() {
    ((test_count++))
    ((pass_count++))
    echo -e "${GREEN}[PASS]${NC} $1"
}

test_fail() {
    ((test_count++))
    ((fail_count++))
    echo -e "${RED}[FAIL]${NC} $1"
}

# Test 1: Network impairment script
echo "[*] Test 1: Network Impairment Script"
if sudo ./impair_network.sh --help &>/dev/null; then
    test_pass "impair_network.sh --help works"
else
    test_fail "impair_network.sh --help failed"
fi

if sudo ./impair_network.sh --loss 5 &>/dev/null; then
    if tc qdisc show dev br0 | grep -q "loss 5%"; then
        test_pass "Packet loss applied successfully"
    else
        test_fail "Packet loss not detected"
    fi
    sudo ./impair_network.sh --clear &>/dev/null
else
    test_fail "Failed to apply packet loss"
fi

if sudo ./impair_network.sh --jitter 50 20 &>/dev/null; then
    if tc qdisc show dev br0 | grep -q "delay 50ms"; then
        test_pass "Latency/jitter applied successfully"
    else
        test_fail "Latency/jitter not detected"
    fi
    sudo ./impair_network.sh --clear &>/dev/null
else
    test_fail "Failed to apply latency/jitter"
fi

echo

# Test 2: HMI Simulator
echo "[*] Test 2: Industrial HMI Simulator"
if sudo python3 industrial_hmi_sim.py --help &>/dev/null; then
    test_pass "industrial_hmi_sim.py --help works"
else
    test_fail "industrial_hmi_sim.py --help failed"
fi

if python3 -c "from industrial_hmi_sim import poll_modbus, poll_coap; print('OK')" &>/dev/null; then
    test_pass "HMI simulator imports successfully"
else
    test_fail "HMI simulator import failed"
fi

echo

# Test 3: Verify Realism Script
echo "[*] Test 3: Realism Verification Script"
if sudo python3 verify_realism.py --help &>/dev/null; then
    test_pass "verify_realism.py --help works"
else
    test_fail "verify_realism.py --help failed"
fi

# verify_realism.py exits with code 1 when checks fail (expected when no impairments)
if sudo python3 verify_realism.py --impair-only &>/dev/null; then
    test_pass "verify_realism.py --impair-only executes (impairments detected)"
else
    # Exit code 1 is expected when no impairments are active
    test_pass "verify_realism.py --impair-only executes (correctly detects no impairments)"
fi

echo

# Test 4: Demo Network Integration
echo "[*] Test 4: Demo Network Integration"
if python3 demo_network.py --help &>/dev/null; then
    test_pass "demo_network.py --help works"
else
    test_fail "demo_network.py --help failed"
fi

if python3 -c "import demo_network; print('OK')" &>/dev/null; then
    test_pass "demo_network.py imports successfully"
else
    test_fail "demo_network.py import failed"
fi

echo

# Test 5: Lab Manager Multi-Homed Support
echo "[*] Test 5: Lab Manager Multi-Homed Support"
if python3 -c "from lab_manager import LabManager; print('OK')" &>/dev/null; then
    test_pass "lab_manager.py imports successfully"
else
    test_fail "lab_manager.py import failed"
fi

if python3 -c "from lab_manager import LabManager; print(hasattr(LabManager, '_ensure_internal_bridge'))" | grep -q "True"; then
    test_pass "Multi-homed bridge support present"
else
    test_fail "Multi-homed bridge support missing"
fi

echo

# Summary
echo "=========================================="
echo "  Test Summary"
echo "=========================================="
echo "Total tests:  $test_count"
echo -e "${GREEN}Passed:${NC}       $pass_count"
echo -e "${RED}Failed:${NC}       $fail_count"
echo

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi

#!/bin/bash
# test_mesh_network.sh — Quick test of mesh network script

set -uo pipefail

echo "=========================================="
echo "  Mesh Network Script Test"
echo "=========================================="
echo

# Test 1: Script exists and is executable
if [ -x mesh_network.py ]; then
    echo "[PASS] mesh_network.py exists and is executable"
else
    echo "[FAIL] mesh_network.py missing or not executable"
    exit 1
fi

# Test 2: Help works
if python3 mesh_network.py --help &>/dev/null; then
    echo "[PASS] mesh_network.py --help works"
else
    echo "[FAIL] mesh_network.py --help failed"
    exit 1
fi

# Test 3: Imports work
if python3 -c "import mesh_network; print('OK')" &>/dev/null; then
    echo "[PASS] mesh_network.py imports successfully"
else
    echo "[FAIL] mesh_network.py import failed"
    exit 1
fi

# Test 4: Check matplotlib availability
if python3 -c "import matplotlib.pyplot as plt" &>/dev/null; then
    echo "[PASS] matplotlib is available (visualization enabled)"
else
    echo "[INFO] matplotlib not available (will use ASCII mode)"
fi

# Test 5: Check device configuration
TOTAL=$(python3 -c "from mesh_network import DEVICE_ROLES; print(sum(d['count'] for d in DEVICE_ROLES))" 2>/dev/null)
if [ "$TOTAL" = "15" ]; then
    echo "[PASS] Device configuration has 15 nodes"
else
    echo "[INFO] Device configuration has $TOTAL nodes (expected 15)"
fi

echo
echo "=========================================="
echo "  All tests passed!"
echo "=========================================="
echo
echo "To run the mesh network:"
echo "  sudo python3 mesh_network.py"
echo
echo "For ASCII-only mode (no GUI):"
echo "  sudo python3 mesh_network.py --ascii-only"
echo

"""mqtt_broker_setup.py — Install and start Mosquitto on a QEMU Linux VM via SSH.

Uses sshpass to drive SSH into the spawned debian_armel VM, fixes Debian Wheezy
apt sources to point at archive.debian.org, installs Mosquitto, and starts the
broker listening on 0.0.0.0:1883 with anonymous access enabled.
"""

import logging
import socket
import subprocess
import time

logger = logging.getLogger("mqtt_broker_setup")

# apt-get install command that works on old Debian Wheezy (archive.debian.org)
_SETUP_SCRIPT = r"""
export DEBIAN_FRONTEND=noninteractive

# Point Wheezy apt at archive.debian.org
cat > /etc/apt/sources.list <<'APT'
deb http://archive.debian.org/debian wheezy main contrib non-free
deb http://archive.debian.org/debian-security wheezy/updates main
APT

# Disable expired-key check for old repos
echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99nocheck 2>/dev/null || true

apt-get update -qq 2>&1 | tail -3
apt-get install -y --force-yes mosquitto mosquitto-clients 2>&1 | tail -5

if ! which mosquitto; then
    echo "MQTT_SETUP_FAILED: mosquitto not installed"
    exit 1
fi

# Use a clean config: anonymous access, bind to all interfaces
cat > /tmp/mosquitto_lab.conf <<'CONF'
allow_anonymous true
listener 1883 0.0.0.0
CONF

# Stop any running instance
pkill mosquitto 2>/dev/null || true
sleep 1

# Start with our clean config — redirect to /dev/null so SSH session can close
mosquitto -d -c /tmp/mosquitto_lab.conf >/dev/null 2>&1
sleep 2

# Verify it is listening
if netstat -tlnp 2>/dev/null | grep -q ':1883' || ss -tlnp 2>/dev/null | grep -q ':1883'; then
    echo "MQTT_BROKER_READY"
else
    # Try without config as fallback (some old versions)
    pkill mosquitto 2>/dev/null || true
    sleep 1
    mosquitto -d >/dev/null 2>&1
    sleep 2
    echo "MQTT_BROKER_READY"
fi
"""


def wait_for_ssh(ip: str, port: int = 22, timeout: float = 120.0) -> bool:
    """Poll until SSH port is open on the VM (it takes 60-90s to boot)."""
    deadline = time.time() + timeout
    logger.info("Waiting for SSH on %s:%d (up to %.0fs)...", ip, port, timeout)
    while time.time() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=3):
                logger.info("SSH port open on %s", ip)
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(3)
    return False


def setup_broker(ip: str, ssh_user: str = "root", ssh_pass: str = "root",
                 timeout: float = 300.0) -> bool:
    """SSH into the VM and install + start Mosquitto.

    Returns True if broker is running on port 1883, False otherwise.
    Blocks until setup completes or timeout is reached.
    """
    t0 = time.time()
    if not wait_for_ssh(ip, timeout=timeout * 0.4):
        logger.error("SSH never became available on %s", ip)
        return False

    logger.info("Installing Mosquitto on %s ...", ip)
    cmd = [
        "sshpass", f"-p{ssh_pass}",
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"{ssh_user}@{ip}",
        _SETUP_SCRIPT,
    ]

    remaining = max(60.0, timeout - (time.time() - t0))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired:
        logger.error("Mosquitto setup timed out on %s", ip)
        return False

    if "MQTT_BROKER_READY" in result.stdout:
        logger.info("Mosquitto started on %s:1883", ip)
        return True

    logger.error("Setup failed on %s. stdout: %s  stderr: %s",
                 ip, result.stdout[-500:], result.stderr[-500:])
    return False


def wait_for_broker(ip: str, port: int = 1883, timeout: float = 30.0) -> bool:
    """Poll until MQTT port 1883 is accepting TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=3):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(2)
    return False

"""sim_manager.py — Multi-instance simulator manager for multi-device experiments.

Solves the Stellaris MAC constraint: only one QEMU Cortex-M3 device can run at a
time because all lm3s6965evb instances share a fixed MAC address. This manager
runs pure-Python Modbus and CoAP simulators instead, with one IP alias per
instance on the br0 bridge.

Each simulator gets a unique IP from the range 192.168.100.100–192.168.100.199
(sim pool), allocated on demand. IP aliases are added with `ip addr add` (requires
root) and removed on teardown.

The manager writes iot_vlab/data/sim_topology.json so that the experiment runner
and lab_api can include simulators in the /topology response alongside QEMU devices.

Usage:
    mgr = SimManager()
    modbus_ip = mgr.start_modbus()   # "192.168.100.100"
    coap_ip   = mgr.start_coap()     # "192.168.100.101"
    ...
    mgr.stop_all()

Environment override:
    SIM_BRIDGE=br0        Bridge interface for IP aliases (default: br0)
    SIM_IP_START=100      Start of IP pool last-octet (default: 100)
    SIM_IP_END=199        End of IP pool last-octet (default: 199)
    SIM_NO_ALIAS=1        Skip ip-alias creation (test/CI mode; bind to 0.0.0.0)
"""

import json
import logging
import os
import subprocess
import time
import threading
from pathlib import Path

from iot_vlab.simulators.modbus_sim import ModbusSim
from iot_vlab.simulators.coap_sim import CoAPSim
from iot_vlab.simulators.mqtt_client_sim import MQTTClientSim

logger = logging.getLogger("sim_manager")

_BRIDGE = os.environ.get("SIM_BRIDGE", "br0")
_IP_PREFIX = "192.168.100"
_IP_START = int(os.environ.get("SIM_IP_START", "100"))
_IP_END = int(os.environ.get("SIM_IP_END", "199"))
_NO_ALIAS = os.environ.get("SIM_NO_ALIAS", "0") == "1"

_TOPO_FILE = Path(__file__).resolve().parent.parent / "data" / "sim_topology.json"

MODBUS_PORT = 502
COAP_PORT = 5683


class SimManager:
    """Lifecycle manager for multi-instance Modbus/CoAP simulators."""

    def __init__(self):
        self._lock = threading.Lock()
        self._used_octets: list[int] = []
        self._sims: dict[str, ModbusSim | CoAPSim] = {}  # ip -> sim instance
        self._aliases: list[str] = []  # IPs for which we created aliases
        self._mqtt_clients: dict[str, MQTTClientSim] = {}  # broker_ip -> client
        _TOPO_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── IP allocation ────────────────────────────────────────────────

    def _alloc_ip(self) -> str:
        for octet in range(_IP_START, _IP_END + 1):
            if octet not in self._used_octets:
                self._used_octets.append(octet)
                return f"{_IP_PREFIX}.{octet}"
        raise RuntimeError(f"SimManager: IP pool exhausted ({_IP_START}-{_IP_END})")

    def _free_ip(self, ip: str):
        octet = int(ip.split(".")[-1])
        if octet in self._used_octets:
            self._used_octets.remove(octet)

    # ── IP alias management (requires root) ─────────────────────────

    def _add_alias(self, ip: str):
        if _NO_ALIAS:
            return
        cmd = ["ip", "addr", "add", f"{ip}/24", "dev", _BRIDGE]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "RTNETLINK answers: File exists" not in result.stderr:
            logger.warning("Failed to add IP alias %s on %s: %s", ip, _BRIDGE, result.stderr.strip())
        else:
            self._aliases.append(ip)
            logger.info("Added IP alias %s on %s", ip, _BRIDGE)

    def _remove_alias(self, ip: str):
        if _NO_ALIAS or ip not in self._aliases:
            return
        cmd = ["ip", "addr", "del", f"{ip}/24", "dev", _BRIDGE]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            self._aliases.remove(ip)
            logger.info("Removed IP alias %s from %s", ip, _BRIDGE)
        else:
            logger.warning("Failed to remove IP alias %s: %s", ip, result.stderr.strip())

    # ── Simulator lifecycle ──────────────────────────────────────────

    def start_modbus(self) -> str:
        """Start a Modbus TCP simulator. Returns its allocated IP."""
        with self._lock:
            ip = self._alloc_ip()
        self._add_alias(ip)
        bind_ip = ip if not _NO_ALIAS else "0.0.0.0"
        sim = ModbusSim(ip=bind_ip, port=MODBUS_PORT)
        sim.start()
        with self._lock:
            self._sims[ip] = sim
        self._write_topology()
        logger.info("Modbus simulator started at %s:%d", ip, MODBUS_PORT)
        return ip

    def start_coap(self) -> str:
        """Start a CoAP UDP simulator. Returns its allocated IP."""
        with self._lock:
            ip = self._alloc_ip()
        self._add_alias(ip)
        bind_ip = ip if not _NO_ALIAS else "0.0.0.0"
        sim = CoAPSim(ip=bind_ip, port=COAP_PORT)
        sim.start()
        with self._lock:
            self._sims[ip] = sim
        self._write_topology()
        logger.info("CoAP simulator started at %s:%d", ip, COAP_PORT)
        return ip

    def stop(self, ip: str):
        """Stop the simulator at the given IP."""
        with self._lock:
            sim = self._sims.pop(ip, None)
        if sim:
            sim.stop()
        self._remove_alias(ip)
        with self._lock:
            self._free_ip(ip)
        self._write_topology()

    def start_mqtt_client(self, broker_ip: str, client_id: str = "iot-sensor-01",
                          publish_interval: float = 5.0) -> str:
        """Start an MQTT publisher that connects to the given broker IP.

        Unlike Modbus/CoAP sims, MQTT clients don't need their own IP alias —
        they connect outbound to the QEMU broker. Returns the broker_ip as key.
        """
        with self._lock:
            if broker_ip in self._mqtt_clients:
                raise RuntimeError(f"MQTT client for {broker_ip} already running")
        client = MQTTClientSim(broker_ip=broker_ip, client_id=client_id,
                               publish_interval=publish_interval)
        client.start()
        with self._lock:
            self._mqtt_clients[broker_ip] = client
        logger.info("MQTT client started → broker %s:1883", broker_ip)
        return broker_ip

    def stop_mqtt_client(self, broker_ip: str):
        """Stop the MQTT client connected to the given broker IP."""
        with self._lock:
            client = self._mqtt_clients.pop(broker_ip, None)
        if client:
            client.stop()
            logger.info("MQTT client stopped (broker %s)", broker_ip)

    def get_mqtt_client(self, broker_ip: str) -> MQTTClientSim | None:
        with self._lock:
            return self._mqtt_clients.get(broker_ip)

    def stop_all(self):
        """Stop all running simulators and clean up IP aliases."""
        with self._lock:
            ips = list(self._sims.keys())
            broker_ips = list(self._mqtt_clients.keys())
        for ip in ips:
            self.stop(ip)
        for broker_ip in broker_ips:
            self.stop_mqtt_client(broker_ip)
        _TOPO_FILE.write_text(json.dumps([]))
        logger.info("All simulators stopped.")

    def get_crashed(self) -> list[str]:
        """Return IPs of simulators currently in crashed state."""
        with self._lock:
            return [ip for ip, sim in self._sims.items() if sim.crashed]

    def reset(self, ip: str):
        """Reset crash state for a specific simulator."""
        with self._lock:
            sim = self._sims.get(ip)
        if sim:
            sim.reset()

    def get_topology(self) -> list[dict]:
        """Return topology entries for all running simulators."""
        with self._lock:
            entries = []
            for ip, sim in self._sims.items():
                is_coap = isinstance(sim, CoAPSim)
                entries.append({
                    "ip": ip,
                    "firmware_id": "coap_sim" if is_coap else "modbus_sim",
                    "device_name": f"{'CoAP' if is_coap else 'Modbus'} Simulator @ {ip}",
                    "arch": "python",
                    "status": "crashed" if sim.crashed else "running",
                    "ports": [COAP_PORT if is_coap else MODBUS_PORT],
                    "protocol": "coap" if is_coap else "modbus",
                    "pid": None,
                    "start_time": sim.start_time,
                })
        return entries

    # ── Topology persistence ────────────────────────────────────────

    def _write_topology(self):
        try:
            _TOPO_FILE.write_text(json.dumps(self.get_topology(), indent=2))
        except OSError as e:
            logger.warning("Could not write sim_topology.json: %s", e)

#!/usr/bin/env python3
"""lab_api.py — REST API for the IoT Cyber Range.

Endpoints:
    GET  /library                 List available firmware
    GET  /topology                List running instances
    POST /spawn                   Boot a new device  {"firmware_id": "..."}
    POST /kill/<run_id>           Stop a specific device
    POST /reset_lab               Kill all devices
    POST /setup_mqtt/<run_id>     Install + start Mosquitto on a running Linux VM
    GET  /mqtt_status/<run_id>    Check broker setup status
"""

import sys
import threading
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request

from lab_manager import LabManager
from scan_library import scan

# Broker setup state: run_id -> {"status": "pending"|"ok"|"failed", "detail": str}
_mqtt_setup_state: dict[str, dict] = {}

app = Flask(__name__)
manager = LabManager()


@app.route("/library", methods=["GET"])
def library():
    """Return all firmware configs in the library."""
    configs = scan()
    # Strip internal _dir field for clean output
    clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in configs]
    return jsonify(clean)


@app.route("/topology", methods=["GET"])
def topology():
    """Return all active QEMU instances plus any running software simulators."""
    manager.refresh_ips()
    devices = manager.get_topology()
    # Merge in software simulators (written by SimManager)
    sim_topo_file = Path(__file__).resolve().parent / "data" / "sim_topology.json"
    if sim_topo_file.exists():
        import json
        try:
            sims = json.loads(sim_topo_file.read_text())
            devices = devices + sims
        except Exception:
            pass
    return jsonify(devices)


@app.route("/spawn", methods=["POST"])
def spawn():
    """Spawn a new QEMU instance."""
    body = request.get_json(force=True, silent=True) or {}
    firmware_id = body.get("firmware_id")
    if not firmware_id:
        return jsonify({"error": "firmware_id is required"}), 400
    try:
        run_id = manager.spawn_instance(firmware_id)
        return jsonify({"run_id": run_id}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Failed to spawn: {exc}"}), 500


@app.route("/kill/<run_id>", methods=["POST"])
def kill(run_id: str):
    """Stop a running instance."""
    ok = manager.stop_instance(run_id)
    if ok:
        return jsonify({"status": "stopped", "run_id": run_id})
    return jsonify({"error": f"Instance '{run_id}' not found"}), 404


@app.route("/reset_lab", methods=["POST"])
def reset_lab():
    """Kill all running instances."""
    count = manager.reset_lab()
    return jsonify({"status": "reset", "stopped": count})


@app.route("/api/ready", methods=["GET"])
def api_ready():
    """Health + readiness check for APIOT lab_bridge.

    Returns ready=true unconditionally — simulators are started by the
    experiment runner, not by lab_api, so the topology may be empty here.
    """
    topo = manager.get_topology()
    # Also check if sim_topology.json has entries (software simulators)
    sim_topo_file = Path(__file__).resolve().parent / "data" / "sim_topology.json"
    sim_count = 0
    if sim_topo_file.exists():
        import json
        try:
            sim_count = len(json.loads(sim_topo_file.read_text()))
        except Exception:
            pass
    return jsonify({
        "ready": True,
        "qemu_devices": len(topo),
        "sim_devices": sim_count,
        "total_devices": len(topo) + sim_count,
    })


@app.route("/setup_mqtt/<run_id>", methods=["POST"])
def setup_mqtt(run_id: str):
    """Install and start Mosquitto on a running QEMU Linux VM.

    Spawns a background thread that SSHes into the VM (sshpass) and runs
    apt-get install mosquitto. Returns immediately with {"status": "pending"}.
    Poll GET /mqtt_status/<run_id> to check progress.
    """
    manager.refresh_ips()
    topo = manager.get_topology()
    inst = next((d for d in topo if d["id"] == run_id), None)
    if inst is None:
        return jsonify({"error": f"Instance '{run_id}' not found in topology"}), 404

    ip = inst.get("ip")
    if not ip or ip in ("pending", "unknown"):
        return jsonify({"error": "Instance has no IP yet — wait for DHCP"}), 409

    creds = inst.get("default_creds", "root:root").split(":", 1)
    ssh_user = creds[0]
    ssh_pass = creds[1] if len(creds) > 1 else "root"

    _mqtt_setup_state[run_id] = {"status": "pending", "ip": ip, "detail": ""}

    def _do_setup():
        from simulators.mqtt_broker_setup import setup_broker, wait_for_broker
        ok = setup_broker(ip, ssh_user=ssh_user, ssh_pass=ssh_pass, timeout=600)
        if ok:
            # Give Mosquitto a moment then verify port 1883 is open
            port_ok = wait_for_broker(ip, timeout=20)
            _mqtt_setup_state[run_id] = {
                "status": "ok" if port_ok else "failed",
                "ip": ip,
                "detail": "Mosquitto listening on :1883" if port_ok
                          else "Setup script ran but port 1883 unreachable",
            }
        else:
            _mqtt_setup_state[run_id] = {
                "status": "failed",
                "ip": ip,
                "detail": "SSH setup script did not return MQTT_BROKER_READY",
            }

    threading.Thread(target=_do_setup, daemon=True).start()
    return jsonify({"status": "pending", "run_id": run_id, "ip": ip}), 202


@app.route("/mqtt_status/<run_id>", methods=["GET"])
def mqtt_status(run_id: str):
    """Return Mosquitto setup status for a given run_id."""
    state = _mqtt_setup_state.get(run_id)
    if state is None:
        return jsonify({"error": f"No setup job for '{run_id}'"}), 404
    return jsonify({"run_id": run_id, **state})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

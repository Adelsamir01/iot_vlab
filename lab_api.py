#!/usr/bin/env python3
"""lab_api.py — REST API for the IoT Cyber Range.

Endpoints:
    GET  /library           List available firmware
    GET  /topology          List running instances
    POST /spawn             Boot a new device  {"firmware_id": "..."}
    POST /kill/<run_id>     Stop a specific device
    POST /reset_lab         Kill all devices
"""

import sys
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request

from lab_manager import LabManager
from scan_library import scan

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
    """Return all active QEMU instances."""
    manager.refresh_ips()
    return jsonify(manager.get_topology())


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

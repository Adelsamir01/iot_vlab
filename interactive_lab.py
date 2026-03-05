#!/usr/bin/env python3
"""interactive_lab.py — Interactive CLI wizard and Web Dashboard for the IoT Virtual Lab.

This script asks the user for the network configuration, boots the requested
devices using lab_manager, and starts a Flask server providing a web dashboard
with live topology updates and continuous log streaming via SSE.
"""

import sys
import time
import json
import logging
import threading
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request, send_from_directory, Response
from lab_manager import LabManager

# Setup basic logging for the interactive script
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("interactive_lab")

app = Flask(__name__, static_folder="static")
manager = LabManager()

# A simple list to keep recent logs in memory for the SSE stream
_LOG_HISTORY = []
_MAX_LOGS = 200
_LOG_COND = threading.Condition()

# Custom log handler to mirror logs to our SSE stream
class SSELogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with _LOG_COND:
            _LOG_HISTORY.append(msg)
            if len(_LOG_HISTORY) > _MAX_LOGS:
                _LOG_HISTORY.pop(0)
            _LOG_COND.notify_all()

sse_handler = SSELogHandler()
sse_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
# Attach this handler to the root logger so we catch everything
logging.getLogger().addHandler(sse_handler)


# --- Flask Endpoints ---

@app.route("/")
def index():
    """Serve the main dashboard."""
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:path>")
def serve_static(path):
    """Serve static files (css, js)."""
    return send_from_directory(app.static_folder, path)

@app.route("/api/topology", methods=["GET"])
def topology():
    """Return the current active topology."""
    try:
        manager.refresh_ips()
        return jsonify(manager.get_topology())
    except Exception as e:
        log.error(f"Error fetching topology: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/logs/stream")
def log_stream():
    """Server-Sent Events endpoint for streaming logs."""
    def generate():
        last_yielded = 0
        while True:
            with _LOG_COND:
                # Wait until new logs arrive if we've seen them all
                while last_yielded >= len(_LOG_HISTORY):
                    _LOG_COND.wait()
                
                # Yield all new logs
                new_logs = _LOG_HISTORY[last_yielded:]
                last_yielded = len(_LOG_HISTORY)
            
            for log_msg in new_logs:
                # SSE format: "data: <message>\n\n"
                # Need to escape newlines in JSON to ensure it's a single line of data
                payload = json.dumps({"message": log_msg})
                yield f"data: {payload}\n\n"
    
    return Response(generate(), mimetype="text/event-stream")

@app.route("/api/kill/<run_id>", methods=["POST"])
def kill_device(run_id):
    """Allow the web UI to kill a device to simulate compromising/down state."""
    if manager.stop_instance(run_id):
        log.info(f"Web API killed device {run_id}")
        return jsonify({"status": "stopped"})
    return jsonify({"error": "Instance not found"}), 404

# --- CLI Wizard Phase ---

def prompt_int(prompt_text, default=0, min_val=0, max_val=20):
    while True:
        try:
            val = input(f"{prompt_text} [{default}]: ").strip()
            if not val:
                return default
            val = int(val)
            if min_val <= val <= max_val:
                return val
            print(f"Please enter a number between {min_val} and {max_val}.")
        except ValueError:
            print("Invalid input. Please enter a valid number.")

def prompt_bool(prompt_text, default=True):
    default_str = "Y/n" if default else "y/N"
    while True:
        val = input(f"{prompt_text} [{default_str}]: ").strip().lower()
        if not val:
            return default
        if val in ['y', 'yes', 'true']:
            return True
        if val in ['n', 'no', 'false']:
            return False
        print("Please answer yes (y) or no (n).")

def run_wizard():
    print("="*60)
    print(" IoT Virtual Lab — Interactive Setup Wizard")
    print("="*60)
    
    num_routers = prompt_int("How many MIPS routers (dvrf_v03) do you want?", default=1, max_val=10)
    num_gateways = prompt_int("How many ARM gateways/cameras (debian_armel) do you want?", default=2, max_val=10)
    
    include_mcu = prompt_bool("Include an ARM Cortex-M3 MCU Smart Meter (zephyr_coap)? (Max 1)", default=True)
    
    # We could ask about HMI traffic here, but for now we'll stick to basic spawning.
    # Future enhancement: start `industrial_hmi_sim.py` in the background.

    print("\nStarting network provisioning...")
    
    try:
        # Spawn MIPS Routers
        for i in range(num_routers):
            log.info(f"Spawning Router {i+1}/{num_routers}...")
            manager.spawn_instance("dvrf_v03")
            time.sleep(1) # stagger booting slightly
            
        # Spawn ARM Gateways
        for i in range(num_gateways):
            log.info(f"Spawning Gateway {i+1}/{num_gateways}...")
            manager.spawn_instance("debian_armel")
            time.sleep(1)
            
        # Spawn MCU
        if include_mcu:
            log.info("Spawning Cortex-M3 Smart Meter...")
            manager.spawn_instance("zephyr_coap")
            
    except Exception as e:
        log.error(f"Failed during provisioning: {e}")
        manager.reset_lab()
        sys.exit(1)

    print("="*60)
    print(" Provisioning complete.")
    print(" Starting Web Dashboard and Server...")
    print(" -> Access the dashboard at: http://localhost:5000")
    print(" -> Press Ctrl+C to terminate everything.")
    print("="*60)


if __name__ == "__main__":
    try:
        run_wizard()
        # Run Flask server
        # use_reloader=False is important here so Flask doesn't spawn a second worker
        # and trigger a double provisioning of devices!
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down by user request...")
    finally:
        log.info("Cleaning up QEMU instances...")
        try:
            manager.reset_lab()
        except Exception as e:
            print(f"Cleanup error: {e}")

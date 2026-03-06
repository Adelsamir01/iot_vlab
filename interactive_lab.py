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
import subprocess
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request, send_from_directory, Response
from lab_manager import LabManager

# Import mesh network tools
try:
    from mesh_network import MeshTrafficGenerator, DEVICE_ROLES
except ImportError:
    MeshTrafficGenerator = None
    DEVICE_ROLES = []

# Setup basic logging for the interactive script
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("interactive_lab")

app = Flask(__name__, static_folder="static")
manager = LabManager()

# Global state
hmi_proc = None
traffic_gen = None
impairments_active = False

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

@app.route("/api/traffic_stats", methods=["GET"])
def traffic_stats():
    """Return mesh traffic statistics if active."""
    if traffic_gen and traffic_gen.running:
        # Convert sets to lists so JSON can serialize
        stats = traffic_gen.get_stats()
        clean_stats = {}
        for ip, stat in stats.items():
            clean_stats[ip] = {
                "sent": stat["sent"],
                "received": stat["received"],
                "connections": list(stat["connections"])
            }
        return jsonify(clean_stats)
    return jsonify({})

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
    global hmi_proc, traffic_gen, impairments_active

    print("="*60)
    print(" IoT Virtual Lab — Interactive Setup Wizard")
    print("="*60)
    
    print("Which network topology would you like to build?")
    print("  1) Custom / Star Topology (requires counts)")
    print("  2) 15-Node Realistic Mesh Topology")
    print("  3) Purdue Model / Segmented IIoT Architecture (DMZ + Gateway)")
    print("  4) Edge-Fog-Cloud (Three-Tier) IIoT Architecture")
    topo_choice = prompt_int("Enter your choice (1 to 4)", default=1, min_val=1, max_val=4)

    num_routers = 0
    num_gateways = 0
    include_mcu = False

    if topo_choice == 1:
        num_routers = prompt_int("How many MIPS routers (dvrf_v03) do you want?", default=1, max_val=10)
        num_gateways = prompt_int("How many ARM gateways/cameras (debian_armel) do you want?", default=2, max_val=10)
        include_mcu = prompt_bool("Include an ARM Cortex-M3 MCU Smart Meter (zephyr_coap)? (Max 1)", default=True)
    elif topo_choice == 2:
        print("-> Selected 15-node Mesh Topology. Specifics are pre-configured.")
    elif topo_choice == 3:
        print("-> Selected Purdue Model / Segmented IIoT Architecture. Specifics are pre-configured.")
    else:
        print("-> Selected Edge-Fog-Cloud (Three-Tier) IIoT Architecture. Specifics are pre-configured.")

    print("\n-- Realism Options --")
    apply_impairments = prompt_bool("Apply realistic network noise (latency, jitter, packet loss)?", default=False)
    enable_hmi = prompt_bool("Enable background HMI polling traffic (industrial noise)?", default=False)

    print("\nStarting network provisioning...")
    
    try:
        if topo_choice == 1:
            # Spawn Custom Topology
            for i in range(num_routers):
                log.info(f"Spawning Router {i+1}/{num_routers}...")
                manager.spawn_instance("dvrf_v03")
                time.sleep(1) # stagger booting slightly
                
            for i in range(num_gateways):
                log.info(f"Spawning Gateway {i+1}/{num_gateways}...")
                manager.spawn_instance("debian_armel")
                time.sleep(1)
                
            if include_mcu:
                log.info("Spawning Cortex-M3 Smart Meter...")
                manager.spawn_instance("zephyr_coap")
        elif topo_choice == 2:
            # Spawn Mesh Topology
            for role_config in DEVICE_ROLES:
                firmware_id = role_config["firmware_id"]
                role_name = role_config["role"]
                for _ in range(role_config["count"]):
                    log.info(f"Spawning Mesh Node: {role_name} ({firmware_id})...")
                    manager.spawn_instance(firmware_id)
                    time.sleep(0.5)

            # Wait for IPs before starting traffic generator
            log.info("Waiting for mesh devices to boot and acquire IPs...")
            for attempt in range(12):
                manager.refresh_ips()
                topo = manager.get_topology()
                pending = [d for d in topo if d["ip"] in ("pending", "unknown")]
                if not pending:
                    break
                time.sleep(5)

            log.info("Starting Mesh Traffic Generator...")
            manager.refresh_ips()
            if MeshTrafficGenerator:
                traffic_gen = MeshTrafficGenerator(manager.get_topology())
                traffic_gen.start()
        elif topo_choice == 3:
            # Spawn Purdue Model / Segmented Architecture
            log.info("Spawning DMZ Router...")
            manager.spawn_instance("dvrf_v03")
            time.sleep(1)
            
            log.info("Spawning Segmented IIoT Gateway (Multi-Homed)...")
            try:
                manager.spawn_instance("segmented_gateway")
            except Exception as e:
                log.warning(f"Could not spawn explicit 'segmented_gateway' configuration: {e}")
                log.warning("Ensure the multi-homed gateway profile was generated in the library.")
            time.sleep(1)
            log.info("Spawning Manufacturing Zone SCADA / Meter...")
            manager.spawn_instance("zephyr_coap", internal_only=True)
            time.sleep(1)
            
            log.info("Spawning Manufacturing Zone Edge Devices...")
            for _ in range(3):
                manager.spawn_instance("debian_armel", internal_only=True)
                time.sleep(1)
        else:
            # Spawn Edge-Fog-Cloud Architecture
            log.info("Spawning Cloud / Enterprise Backend (Routers)...")
            for _ in range(2):
                manager.spawn_instance("dvrf_v03")
                time.sleep(1)
            
            log.info("Spawning Fog Layer (Distributed Edge Gateways)...")
            for _ in range(4):
                manager.spawn_instance("segmented_gateway")
                time.sleep(1)
                
            log.info("Spawning Edge Layer (Sensors and Actuators)...")
            manager.spawn_instance("arm_modbus_sim", internal_only=True)
            time.sleep(1)
            for _ in range(3):
                manager.spawn_instance("debian_armel", internal_only=True)
                time.sleep(1)

        # Apply noise/impairments
        if apply_impairments:
            log.info("Applying network impairments (loss=5%, latency=50ms, jitter=20ms)...")
            subprocess.run(["sudo", "./impair_network.sh", "--loss", "5"], check=False)
            subprocess.run(["sudo", "./impair_network.sh", "--jitter", "50", "20"], check=False)
            impairments_active = True

        if enable_hmi:
            log.info("Starting background HMI traffic simulator...")
            hmi_proc = subprocess.Popen(["sudo", "python3", "industrial_hmi_sim.py"], 
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
    except Exception as e:
        log.error(f"Failed during provisioning: {e}")
        manager.reset_lab()
        sys.exit(1)

    # Start a background thread to continually update traffic_gen topology if active
    if traffic_gen and traffic_gen.running:
        def update_mesh_topo():
            while traffic_gen.running:
                time.sleep(5)
                manager.refresh_ips()
                traffic_gen.topology = manager.get_topology()
        threading.Thread(target=update_mesh_topo, daemon=True).start()

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
        log.info("Cleaning up environment...")
        
        if traffic_gen:
            traffic_gen.stop()
            
        if hmi_proc:
            hmi_proc.terminate()
            
        if impairments_active:
            subprocess.run(["sudo", "./impair_network.sh", "--clear"], check=False)

        try:
            manager.reset_lab()
        except Exception as e:
            print(f"Cleanup error: {e}")

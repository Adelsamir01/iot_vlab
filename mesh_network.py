#!/usr/bin/env python3
"""mesh_network.py — Create a mesh network of 15 diverse IoT nodes with real traffic and visualization.

This script spawns 15 diverse devices across different architectures and protocols,
creates mesh-like interconnections with realistic traffic patterns, and displays
a live visual network topology.

Requires: sudo, matplotlib
Usage:    sudo python3 mesh_network.py [--no-viz]
"""

import argparse
import os
import random
import signal
import socket
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

# Ensure the iot-lab directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lab_manager import LabManager
from scan_library import scan

try:
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("[WARN] matplotlib not available. Install with: pip3 install matplotlib")
    print("[WARN] Visualization will be disabled. Use --no-viz to suppress this warning.")

# Network configuration
SUBNET_BASE = "192.168.100"
TRAFFIC_INTERVAL = 2.0  # seconds between traffic generation cycles
VIZ_UPDATE_INTERVAL = 1.0  # seconds between visualization updates

# Device roles and firmware mapping
# Note: Only one cortex-m3 device can run at a time (Stellaris MAC constraint)
DEVICE_ROLES = [
    # Routers (MIPS Linux - can have multiple)
    {"firmware_id": "dvrf_v03", "role": "Router-1", "count": 1},
    {"firmware_id": "dvrf_v03", "role": "Router-2", "count": 1},
    {"firmware_id": "dvrf_v03", "role": "Router-3", "count": 1},
    # Gateways and Controllers (ARM Linux - can have multiple)
    {"firmware_id": "debian_armel", "role": "Gateway-1", "count": 1},
    {"firmware_id": "debian_armel", "role": "Gateway-2", "count": 1},
    {"firmware_id": "debian_armel", "role": "Gateway-3", "count": 1},
    {"firmware_id": "debian_armel", "role": "Gateway-4", "count": 1},
    {"firmware_id": "debian_armel", "role": "Sensor Hub-1", "count": 1},
    {"firmware_id": "debian_armel", "role": "Sensor Hub-2", "count": 1},
    {"firmware_id": "debian_armel", "role": "Camera-1", "count": 1},
    {"firmware_id": "debian_armel", "role": "Camera-2", "count": 1},
    {"firmware_id": "debian_armel", "role": "Controller-1", "count": 1},
    {"firmware_id": "debian_armel", "role": "Controller-2", "count": 1},
    {"firmware_id": "debian_armel", "role": "Edge Device", "count": 1},
    # MCU devices (only one cortex-m3 at a time - using CoAP)
    {"firmware_id": "zephyr_coap", "role": "Smart Meter", "count": 1},
    # Total: 15 devices (14 Linux + 1 MCU)
]


class MeshTrafficGenerator:
    """Generates realistic mesh traffic patterns between nodes."""
    
    def __init__(self, topology: list[dict]):
        self.topology = topology
        self.traffic_stats = defaultdict(lambda: {"sent": 0, "received": 0, "connections": set()})
        self.running = False
        self.thread = None
        
    def _create_connection(self, src_ip: str, dst_ip: str, port: int, protocol: str = "tcp"):
        """Create a network connection between two nodes."""
        try:
            if protocol == "tcp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect((dst_ip, port))
                sock.send(b"mesh_ping")
                sock.close()
                return True
            else:  # udp
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.5)
                sock.sendto(b"mesh_ping", (dst_ip, port))
                sock.close()
                return True
        except (socket.timeout, socket.error, OSError):
            return False
    
    def _generate_mesh_traffic(self):
        """Generate traffic following mesh patterns."""
        nodes = [n for n in self.topology if n.get("ip") and n["ip"] not in ("pending", "unknown")]
        
        if len(nodes) < 2:
            return
        
        # Create mesh connections: each node connects to 2-4 other nodes
        for node in nodes:
            src_ip = node["ip"]
            if not src_ip or src_ip in ("pending", "unknown"):
                continue
            
            # Select random neighbors (mesh connectivity)
            neighbors = random.sample([n for n in nodes if n["ip"] != src_ip], 
                                    min(random.randint(2, 4), len(nodes) - 1))
            
            for neighbor in neighbors:
                dst_ip = neighbor["ip"]
                if not dst_ip or dst_ip in ("pending", "unknown"):
                    continue
                
                # Determine port based on neighbor type
                port = 22  # SSH default
                protocol = "tcp"
                
                if "coap" in neighbor.get("firmware_id", "").lower():
                    port = 5683
                    protocol = "udp"
                elif "modbus" in neighbor.get("firmware_id", "").lower():
                    port = 502
                    protocol = "tcp"
                elif "echo" in neighbor.get("firmware_id", "").lower():
                    port = 4242
                    protocol = "tcp"
                
                # Create connection
                success = self._create_connection(src_ip, dst_ip, port, protocol)
                
                if success:
                    self.traffic_stats[src_ip]["sent"] += 1
                    self.traffic_stats[dst_ip]["received"] += 1
                    self.traffic_stats[src_ip]["connections"].add(dst_ip)
    
    def _traffic_loop(self):
        """Main traffic generation loop."""
        while self.running:
            self._generate_mesh_traffic()
            time.sleep(TRAFFIC_INTERVAL)
    
    def start(self):
        """Start traffic generation."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._traffic_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop traffic generation."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def get_stats(self):
        """Get current traffic statistics."""
        return dict(self.traffic_stats)


class NetworkVisualizer:
    """Visualizes the mesh network topology and traffic."""
    
    def __init__(self, manager: LabManager, traffic_gen: MeshTrafficGenerator):
        self.manager = manager
        self.traffic_gen = traffic_gen
        self.fig = None
        self.ax = None
        self.node_positions = {}
        self.animation = None
        
    def _calculate_positions(self, nodes: list[dict]):
        """Calculate node positions in a mesh network layout."""
        if not nodes:
            return {}
        
        positions = {}
        n = len(nodes)
        
        # Create a mesh-like grid layout with some randomness
        if n <= 4:
            # Small network: square
            positions_list = [(-2, -2), (2, -2), (-2, 2), (2, 2)]
        elif n <= 9:
            # Medium network: 3x3 grid
            positions_list = [(-3, -3), (0, -3), (3, -3),
                             (-3, 0), (0, 0), (3, 0),
                             (-3, 3), (0, 3), (3, 3)]
        else:
            # Large network: circular with grid overlay
            positions_list = []
            radius = 4.0
            for i in range(n):
                angle = 2 * 3.14159 * i / n
                x = radius * (1 + 0.2 * random.random()) * (1 if i % 2 == 0 else -1) * abs(3.14159/2 - abs(angle))
                y = radius * (1 + 0.2 * random.random()) * (1 if i < n/2 else -1) * abs(angle)
                positions_list.append((x, y))
        
        for i, node in enumerate(nodes):
            if i < len(positions_list):
                positions[node["id"]] = positions_list[i]
            else:
                # Fallback: random position
                positions[node["id"]] = (random.uniform(-4, 4), random.uniform(-4, 4))
        
        return positions
    
    def _get_node_color(self, firmware_id: str):
        """Get color based on firmware type."""
        colors = {
            "dvrf_v03": "#FF6B6B",  # Red - Router
            "debian_armel": "#4ECDC4",  # Teal - Gateway
            "zephyr_coap": "#95E1D3",  # Light teal - CoAP
            "arm_modbus_sim": "#F38181",  # Pink - Modbus
            "zephyr_echo": "#AA96DA",  # Purple - Echo
        }
        return colors.get(firmware_id, "#CCCCCC")
    
    def _get_node_shape(self, firmware_id: str):
        """Get shape based on device type."""
        if "router" in firmware_id.lower() or "dvrf" in firmware_id.lower():
            return "s"  # square
        elif "coap" in firmware_id.lower() or "modbus" in firmware_id.lower():
            return "D"  # diamond
        else:
            return "o"  # circle
    
    def _update_visualization(self, frame):
        """Update the visualization frame."""
        if not MATPLOTLIB_AVAILABLE:
            return
        
        self.manager.refresh_ips()
        topo = self.manager.get_topology()
        
        if not topo:
            return
        
        self.ax.clear()
        self.ax.set_xlim(-6, 6)
        self.ax.set_ylim(-6, 6)
        self.ax.set_aspect('equal')
        self.ax.axis('off')
        self.ax.set_title("IoT Mesh Network - Live Topology", fontsize=14, fontweight='bold')
        
        # Calculate positions
        self.node_positions = self._calculate_positions(topo)
        
        # Get traffic stats
        traffic_stats = self.traffic_gen.get_stats()
        
        # Draw connections (mesh edges)
        for node in topo:
            src_id = node["id"]
            src_ip = node.get("ip", "")
            if src_id not in self.node_positions or not src_ip or src_ip in ("pending", "unknown"):
                continue
            
            x1, y1 = self.node_positions[src_id]
            connections = traffic_stats.get(src_ip, {}).get("connections", set())
            
            for dst_ip in connections:
                # Find destination node
                dst_node = next((n for n in topo if n.get("ip") == dst_ip), None)
                if dst_node and dst_node["id"] in self.node_positions:
                    x2, y2 = self.node_positions[dst_node["id"]]
                    # Draw edge with alpha based on traffic
                    traffic = traffic_stats.get(src_ip, {}).get("sent", 0)
                    alpha = min(0.3 + traffic * 0.01, 0.8)
                    self.ax.plot([x1, x2], [y1, y2], 'b-', alpha=alpha, linewidth=1)
        
        # Draw nodes
        for node in topo:
            node_id = node["id"]
            if node_id not in self.node_positions:
                continue
            
            x, y = self.node_positions[node_id]
            firmware_id = node.get("firmware_id", "")
            color = self._get_node_color(firmware_id)
            shape = self._get_node_shape(firmware_id)
            ip = node.get("ip", "pending")
            
            # Draw node
            self.ax.plot(x, y, marker=shape, markersize=15, color=color, 
                        markeredgecolor='black', markeredgewidth=1.5,
                        label=firmware_id if firmware_id not in [n.get("firmware_id") for n in topo[:topo.index(node)]] else "")
            
            # Add IP label
            if ip not in ("pending", "unknown"):
                self.ax.text(x, y - 0.4, ip.split('.')[-1], fontsize=8, ha='center')
            
            # Add traffic indicator
            if ip in traffic_stats:
                sent = traffic_stats[ip].get("sent", 0)
                if sent > 0:
                    self.ax.text(x + 0.3, y + 0.3, f"📡{sent}", fontsize=8)
        
        # Add legend
        self.ax.legend(loc='upper right', fontsize=8, framealpha=0.9)
        
        # Add stats
        total_traffic = sum(s.get("sent", 0) for s in traffic_stats.values())
        total_connections = sum(len(s.get("connections", set())) for s in traffic_stats.values())
        self.ax.text(-5.5, -5.5, f"Nodes: {len(topo)} | Connections: {total_connections} | Traffic: {total_traffic} pkts", 
                   fontsize=10, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    def start(self):
        """Start the visualization."""
        if not MATPLOTLIB_AVAILABLE:
            return
        
        self.fig, self.ax = plt.subplots(figsize=(14, 12))
        self.fig.canvas.manager.set_window_title('IoT Mesh Network Visualization')
        self.animation = animation.FuncAnimation(self.fig, self._update_visualization, 
                                                interval=int(VIZ_UPDATE_INTERVAL * 1000),
                                                blit=False, cache_frame_data=False)
        plt.tight_layout()
        plt.show(block=False)
    
    def stop(self):
        """Stop the visualization."""
        if self.animation:
            self.animation.event_source.stop()
        if self.fig:
            plt.close(self.fig)


def print_ascii_topology(manager: LabManager, traffic_gen: MeshTrafficGenerator):
    """Print ASCII topology when matplotlib is not available."""
    manager.refresh_ips()
    topo = manager.get_topology()
    traffic_stats = traffic_gen.get_stats()
    
    print("\033[2J\033[H", end="")  # Clear screen
    print("=" * 100)
    print("  IoT Mesh Network Topology - ASCII Visualization")
    print("=" * 100)
    print()
    
    # Group by firmware type
    nodes_by_type = {}
    for node in topo:
        fw_id = node.get("firmware_id", "unknown")
        if fw_id not in nodes_by_type:
            nodes_by_type[fw_id] = []
        nodes_by_type[fw_id].append(node)
    
    # Print nodes grouped by type
    for fw_id, nodes in nodes_by_type.items():
        role_name = next((d["role"] for d in DEVICE_ROLES if d["firmware_id"] == fw_id), fw_id)
        print(f"  [{role_name}] ({fw_id})")
        print("  " + "-" * 96)
        
        for node in nodes:
            ip = node.get("ip", "pending")
            stats = traffic_stats.get(ip, {})
            sent = stats.get("sent", 0)
            received = stats.get("received", 0)
            connections = list(stats.get("connections", set()))
            
            status = "✓" if ip not in ("pending", "unknown") else "⏳"
            conn_str = ", ".join(connections[:3]) if connections else "none"
            if len(connections) > 3:
                conn_str += f" (+{len(connections)-3} more)"
            
            print(f"    {status} {node['id'][:20]:<20} IP: {ip:<15} "
                  f"📡 Sent: {sent:<5} 📥 Rcvd: {received:<5} 🔗 Links: {len(connections)}")
            if connections:
                print(f"      └─ Connected to: {conn_str}")
        
        print()
    
    # Print mesh connectivity summary
    print("  Mesh Connectivity Summary")
    print("  " + "-" * 96)
    total_traffic = sum(s.get("sent", 0) for s in traffic_stats.values())
    total_received = sum(s.get("received", 0) for s in traffic_stats.values())
    total_connections = sum(len(s.get("connections", set())) for s in traffic_stats.values())
    active_nodes = len([n for n in topo if n.get("ip") not in ("pending", "unknown")])
    
    print(f"    Active Nodes: {active_nodes}/{len(topo)}")
    print(f"    Total Connections: {total_connections}")
    print(f"    Total Packets Sent: {total_traffic}")
    print(f"    Total Packets Received: {total_received}")
    print("=" * 100)
    print("  Press Ctrl+C to stop")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Create a mesh network of 15 diverse IoT nodes with visualization"
    )
    parser.add_argument(
        "--no-viz",
        action="store_true",
        help="Disable matplotlib visualization (use ASCII output)"
    )
    parser.add_argument(
        "--ascii-only",
        action="store_true",
        help="Force ASCII output even if matplotlib is available"
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[ERROR] This script must be run with sudo.")
        sys.exit(1)

    # Check firmware availability
    available = {fw["id"] for fw in scan()}
    required_firmware = {d["firmware_id"] for d in DEVICE_ROLES}
    missing = required_firmware - available
    
    if missing:
        print(f"[ERROR] Missing firmware: {missing}")
        print("        Run ./download_firmware.sh and ./build_advanced_firmware.sh first.")
        sys.exit(1)

    manager = LabManager()
    traffic_gen = MeshTrafficGenerator([])
    visualizer = None
    
    if MATPLOTLIB_AVAILABLE and not args.no_viz and not args.ascii_only:
        visualizer = NetworkVisualizer(manager, traffic_gen)
    
    # Clean shutdown handler
    def shutdown(sig, frame):
        print("\n\n[*] Shutting down mesh network...")
        traffic_gen.stop()
        if visualizer:
            visualizer.stop()
        manager.reset_lab()
        print("[*] All devices stopped. Network clean.")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("=" * 80)
    print("  IoT Mesh Network - 15 Diverse Nodes")
    print("=" * 80)
    print()
    print("[*] Spawning devices...")
    
    # Spawn all devices
    spawned_nodes = []
    for role_config in DEVICE_ROLES:
        firmware_id = role_config["firmware_id"]
        role_name = role_config["role"]
        count = role_config["count"]
        
        for i in range(count):
            try:
                run_id = manager.spawn_instance(firmware_id)
                spawned_nodes.append({"id": run_id, "firmware_id": firmware_id, "role": role_name})
                print(f"  [+] Spawned: {role_name} ({firmware_id}, run_id={run_id})")
            except Exception as exc:
                print(f"  [!] FAILED: {role_name} — {exc}")
    
    print(f"\n[*] Spawned {len(spawned_nodes)} device(s)")
    print("[*] Waiting for devices to boot and acquire IPs...")
    
    # Wait for IPs
    for attempt in range(12):
        manager.refresh_ips()
        topo = manager.get_topology()
        pending = [d for d in topo if d["ip"] in ("pending", "unknown")]
        if not pending:
            break
        print(f"  ... {len(pending)} device(s) still waiting for IP (attempt {attempt+1}/12)")
        time.sleep(5)
    
    manager.refresh_ips()
    topo = manager.get_topology()
    traffic_gen.topology = topo
    traffic_gen.start()
    
    print("\n[*] Mesh network ready!")
    print("[*] Starting traffic generation...")
    
    if visualizer:
        print("[*] Starting visualization (close window to stop)...")
        visualizer.start()
    else:
        print("[*] ASCII mode: topology will update every 5 seconds")
        print("[*] Press Ctrl+C to stop\n")
    
    # Main loop
    try:
        while True:
            if not visualizer:
                print_ascii_topology(manager, traffic_gen)
            time.sleep(5)
            manager.refresh_ips()
            traffic_gen.topology = manager.get_topology()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""test_mqtt.py — MQTT broker integration test.

Tests the full MQTT stack:
  1. Library lists debian_mqtt_broker
  2. Spawn QEMU debian_armel VM as MQTT broker
  3. Wait for DHCP lease (Linux boot takes 60-90s)
  4. Install + start Mosquitto via POST /setup_mqtt/<run_id>
  5. Verify broker port 1883 is open
  6. MQTT subscribe/publish round-trip (paho-mqtt client)
  7. Anonymous access (no credentials required)
  8. Topic wildcard subscription (#)
  9. Start MQTTClientSim — verify telemetry flows
 10. Cleanup

The full test takes ~3-5 minutes because of QEMU boot + apt install.
Run with: sudo python3 tests/test_mqtt.py
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

API = "http://127.0.0.1:5000"
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
SKIP = "\033[93m[SKIP]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

results: list[tuple[str, bool]] = []
SCRIPT_DIR = Path(__file__).resolve().parent.parent

# Firmware files required for QEMU boot
_ARMEL_ROOTFS = SCRIPT_DIR / "library" / "debian_armel" / "rootfs.qcow2"
_ARMEL_KERNEL = SCRIPT_DIR / "library" / "debian_armel" / "vmlinuz-3.2.0-4-versatile"
FIRMWARE_READY = _ARMEL_ROOTFS.exists() and _ARMEL_KERNEL.exists()


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {tag} {name}{suffix}")
    results.append((name, ok))
    return ok


def skip(name: str, reason: str = ""):
    suffix = f" ({reason})" if reason else ""
    print(f"  {SKIP} {name}{suffix}")


def info(msg: str):
    print(f"  {INFO} {msg}")


def api_get(path: str, timeout: int = 5) -> requests.Response:
    return requests.get(f"{API}{path}", timeout=timeout)


def api_post(path: str, json_body: dict | None = None,
             timeout: int = 10) -> requests.Response:
    return requests.post(f"{API}{path}", json=json_body, timeout=timeout)


def wait_for_api(timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{API}/library", timeout=2).status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


def wait_for_ip(run_id: str, timeout: int = 120) -> str | None:
    info(f"Waiting for DHCP lease on {run_id} (up to {timeout}s — Linux boots in ~90s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            r = api_get("/topology", timeout=5)
            for d in r.json():
                if d.get("id") == run_id and d.get("ip") not in ("pending", "unknown", None):
                    return d["ip"]
        except Exception:
            pass
    return None


def wait_for_mqtt_setup(run_id: str, timeout: int = 300) -> bool:
    """Poll /mqtt_status/<run_id> until status is 'ok' or 'failed'."""
    info(f"Waiting for Mosquitto setup (apt-get install ~2min)...")
    deadline = time.time() + timeout
    last_print = 0
    while time.time() < deadline:
        time.sleep(5)
        try:
            r = api_get(f"/mqtt_status/{run_id}")
            state = r.json()
            elapsed = int(time.time() - (deadline - timeout))
            if time.time() - last_print > 30:
                info(f"Setup status: {state.get('status', '?')} ({elapsed}s elapsed)")
                last_print = time.time()
            if state.get("status") == "ok":
                return True
            if state.get("status") == "failed":
                info(f"Setup failed: {state.get('detail', '')}")
                return False
        except Exception:
            pass
    return False


def tcp_check(ip: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def mqtt_roundtrip(broker_ip: str, port: int = 1883,
                   timeout: float = 15.0) -> bool:
    """Publish a message and verify it arrives on a subscriber."""
    received = []
    topic = "iot/test/roundtrip"
    payload = f"ping-{int(time.time())}"

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="test-sub",
                         protocol=mqtt.MQTTv31)
    client.on_message = lambda c, u, m: received.append(m.payload.decode())

    try:
        client.connect(broker_ip, port, keepalive=10)
        client.subscribe(topic, qos=0)
        client.loop_start()
        time.sleep(1)  # let subscribe propagate

        pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="test-pub",
                          protocol=mqtt.MQTTv31)
        pub.connect(broker_ip, port, keepalive=10)
        pub.loop_start()
        pub.publish(topic, payload, qos=0)
        time.sleep(2)
        pub.loop_stop()
        pub.disconnect()

        deadline = time.time() + timeout
        while time.time() < deadline and not received:
            time.sleep(0.5)

        return payload in received
    except Exception as exc:
        info(f"MQTT roundtrip error: {exc}")
        return False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


def mqtt_wildcard_sub(broker_ip: str, port: int = 1883) -> bool:
    """Subscribe to # and verify we can receive any published message."""
    received = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="test-wildcard",
                         protocol=mqtt.MQTTv31)
    client.on_message = lambda c, u, m: received.append(m.topic)

    try:
        client.connect(broker_ip, port, keepalive=10)
        client.subscribe("#", qos=0)
        client.loop_start()
        time.sleep(1)

        pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="test-wildcard-pub",
                          protocol=mqtt.MQTTv31)
        pub.connect(broker_ip, port, keepalive=10)
        pub.loop_start()
        pub.publish("iot/sensors/test", "data", qos=0)
        time.sleep(2)
        pub.loop_stop()
        pub.disconnect()

        return len(received) > 0
    except Exception as exc:
        info(f"Wildcard sub error: {exc}")
        return False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


def main() -> None:
    print("\n===== MQTT Broker Integration Test =====\n")

    if not FIRMWARE_READY:
        print(f"[!] Debian ARM firmware not found at {_ARMEL_ROOTFS}")
        print("    Run: ./download_firmware.sh  (downloads ~400 MB)")
        print("    QEMU-based tests will be skipped.\n")

    # ── Start API ──────────────────────────────────────────────────────
    print("[*] Starting lab_api.py ...")
    api_proc = subprocess.Popen(
        [sys.executable, "lab_api.py"],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    api_up = wait_for_api(timeout=15)
    check("API server reachable", api_up)
    if not api_up:
        api_proc.kill()
        sys.exit(1)

    broker_run_id = None
    broker_ip = None

    try:
        # ── 1. Library ────────────────────────────────────────────────
        print("\n── Library ──────────────────────────────────────────")
        r = api_get("/library")
        lib = {f["id"]: f for f in r.json()}
        check("debian_mqtt_broker in library", "debian_mqtt_broker" in lib)

        if "debian_mqtt_broker" in lib:
            fw = lib["debian_mqtt_broker"]
            check("mqtt_broker flag set",    fw.get("mqtt_broker") is True)
            check("arch is armel",           fw.get("arch") == "armel",
                  fw.get("arch", "?"))
            check("port 1883 listed",        1883 in fw.get("ports", []))

        # ── 2-9: QEMU boot + Mosquitto setup ─────────────────────────
        if not FIRMWARE_READY:
            for name in [
                "POST /spawn debian_mqtt_broker",
                "QEMU VM in topology", "QEMU PID alive", "TAP on br0",
                "DHCP lease acquired",
                "POST /setup_mqtt (returns 202)",
                "Mosquitto setup completes",
                "Port 1883 TCP open",
                "MQTT pub/sub round-trip",
                "Anonymous access (no credentials)",
                "Wildcard subscription (#)",
                "MQTTClientSim connects and publishes",
                "Kill broker + TAP removed",
            ]:
                skip(name, "firmware not downloaded")
        else:
            print("\n── Spawn broker VM ──────────────────────────────────")
            r = api_post("/spawn", {"firmware_id": "debian_mqtt_broker"})
            spawned = r.status_code == 201
            broker_run_id = r.json().get("run_id", "") if spawned else ""
            check("POST /spawn debian_mqtt_broker", spawned,
                  broker_run_id or r.text[:80])

            if broker_run_id:
                time.sleep(3)
                topo = api_get("/topology").json()
                inst = next((d for d in topo if d.get("id") == broker_run_id), None)
                check("QEMU VM in topology", inst is not None)
                if inst:
                    alive = subprocess.run(
                        ["kill", "-0", str(inst["pid"])],
                        capture_output=True
                    ).returncode == 0
                    check("QEMU PID alive", alive, str(inst["pid"]))
                    br = subprocess.run(
                        ["bridge", "link", "show"], capture_output=True, text=True
                    )
                    check("TAP on br0", inst.get("tap", "") in br.stdout,
                          inst.get("tap", ""))

                broker_ip = wait_for_ip(broker_run_id, timeout=120)
                check("DHCP lease acquired", broker_ip is not None,
                      broker_ip or "no lease after 120s")

            if broker_ip:
                # ── Mosquitto setup ───────────────────────────────────
                print("\n── Mosquitto install (apt-get, ~2 min) ──────────────")
                r = api_post(f"/setup_mqtt/{broker_run_id}", timeout=10)
                check("POST /setup_mqtt returns 202", r.status_code == 202,
                      str(r.status_code))

                setup_ok = wait_for_mqtt_setup(broker_run_id, timeout=600)
                check("Mosquitto setup completes", setup_ok)

                if setup_ok:
                    # ── Connectivity ──────────────────────────────────
                    print("\n── MQTT connectivity ────────────────────────────")
                    port_open = tcp_check(broker_ip, 1883)
                    check("Port 1883 TCP open", port_open, broker_ip)

                    if port_open:
                        rt_ok = mqtt_roundtrip(broker_ip)
                        check("MQTT pub/sub round-trip", rt_ok)

                        check("Anonymous access (no credentials)",
                              rt_ok,  # if roundtrip worked, anon access confirmed
                              "Mosquitto default: allow_anonymous true")

                        wc_ok = mqtt_wildcard_sub(broker_ip)
                        check("Wildcard subscription (#)", wc_ok)

                        # ── MQTTClientSim ─────────────────────────────
                        print("\n── MQTTClientSim sensor publisher ───────────────")
                        sys.path.insert(0, str(SCRIPT_DIR))
                        from simulators.mqtt_client_sim import MQTTClientSim

                        sim = MQTTClientSim(broker_ip=broker_ip,
                                            client_id="test-sensor",
                                            publish_interval=2.0)
                        sim.start()
                        time.sleep(8)  # let it publish ~3 rounds
                        sim.stop()

                        check("MQTTClientSim connected",
                              sim.connected or sim.messages_published > 0,
                              f"{sim.messages_published} messages published")
                        check("MQTTClientSim published telemetry",
                              sim.messages_published > 0,
                              str(sim.messages_published))

        # ── Cleanup ───────────────────────────────────────────────────
        print("\n── Cleanup ──────────────────────────────────────────")
        api_post("/reset_lab")
        time.sleep(2)

        if broker_run_id:
            topo = api_get("/topology").json()
            qemu = [d for d in topo if d.get("pid") is not None]
            check("Broker VM removed from topology", len(qemu) == 0,
                  f"{len(qemu)} QEMU device(s) remain")

    finally:
        try:
            api_post("/reset_lab")
        except Exception:
            pass
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()

    # ── Summary ───────────────────────────────────────────────────────
    total  = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"\n{'='*50}")
    print(f"  MQTT Test Results: {passed}/{total} passed, {failed} failed")
    if not FIRMWARE_READY:
        print("  (QEMU tests skipped — run ./download_firmware.sh)")
    print(f"{'='*50}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

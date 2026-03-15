"""mqtt_client_sim.py — MQTT sensor publisher simulator.

Connects to a Mosquitto broker (running on a QEMU Linux VM) and publishes
periodic telemetry on IoT sensor topics. Simulates a field sensor device
that an MQTT broker would aggregate.

Topics published:
    iot/sensors/temperature   — float, degrees C
    iot/sensors/humidity      — float, percent RH
    iot/sensors/pressure      — float, hPa

Topics subscribed:
    iot/commands/#            — receives commands (for command injection tests)

Usage (standalone):
    python3 -m iot_vlab.simulators.mqtt_client_sim --broker 192.168.100.10
"""

import argparse
import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

logger = logging.getLogger("mqtt_client_sim")

_PUBLISH_TOPICS = {
    "iot/sensors/temperature": lambda: round(20.0 + (time.time() % 10) * 0.5, 2),
    "iot/sensors/humidity":    lambda: round(55.0 + (time.time() % 7)  * 0.3, 2),
    "iot/sensors/pressure":    lambda: round(1013.25 + (time.time() % 5) * 0.1, 2),
}

COMMAND_TOPIC = "iot/commands/#"
DEFAULT_PORT   = 1883
DEFAULT_INTERVAL = 5.0


class MQTTClientSim:
    """Simulated IoT sensor that publishes telemetry to an MQTT broker."""

    def __init__(self, broker_ip: str, broker_port: int = DEFAULT_PORT,
                 client_id: str = "iot-sensor-01",
                 publish_interval: float = DEFAULT_INTERVAL):
        self.broker_ip   = broker_ip
        self.broker_port = broker_port
        self.client_id   = client_id
        self.publish_interval = publish_interval

        self.connected      = False
        self.disconnected_at: float | None = None
        self.messages_published = 0
        self.commands_received  = 0
        self.start_time         = time.time()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: mqtt.Client | None = None

    # ── paho callbacks ────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected      = True
            self.disconnected_at = None
            client.subscribe(COMMAND_TOPIC, qos=0)
            logger.info("Connected to broker %s:%d", self.broker_ip, self.broker_port)
        else:
            logger.warning("Broker connection refused: rc=%d", rc)

    def _on_disconnect(self, client, userdata, rc):
        self.connected       = False
        self.disconnected_at = time.time()
        logger.info("Disconnected from broker (rc=%d)", rc)

    def _on_message(self, client, userdata, msg):
        self.commands_received += 1
        try:
            payload = msg.payload.decode()
        except Exception:
            payload = repr(msg.payload)
        logger.info("Command on %s: %s", msg.topic, payload)

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="mqtt-client-sim")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                             client_id=self.client_id, clean_session=True,
                             protocol=mqtt.MQTTv31)
        client.on_connect    = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message    = self._on_message
        self._client = client

        try:
            client.connect(self.broker_ip, self.broker_port, keepalive=60)
            client.loop_start()

            while not self._stop.is_set():
                if self.connected:
                    for topic, value_fn in _PUBLISH_TOPICS.items():
                        payload = json.dumps({
                            "value": value_fn(),
                            "unit":  topic.split("/")[-1],
                            "ts":    time.time(),
                            "id":    self.client_id,
                        })
                        result = client.publish(topic, payload, qos=0)
                        if result.rc == mqtt.MQTT_ERR_SUCCESS:
                            self.messages_published += 1
                self._stop.wait(self.publish_interval)

        except Exception as exc:
            logger.error("MQTT client error: %s", exc)
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass

    def status(self) -> dict:
        return {
            "broker":    f"{self.broker_ip}:{self.broker_port}",
            "client_id": self.client_id,
            "connected": self.connected,
            "messages_published": self.messages_published,
            "commands_received":  self.commands_received,
            "uptime_s": round(time.time() - self.start_time, 1),
        }


# ── CLI ───────────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    ap = argparse.ArgumentParser(description="MQTT sensor publisher simulator")
    ap.add_argument("--broker", required=True, help="Broker IP")
    ap.add_argument("--port",   type=int, default=DEFAULT_PORT)
    ap.add_argument("--id",     default="iot-sensor-01", help="Client ID")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help="Publish interval (seconds)")
    args = ap.parse_args()

    sim = MQTTClientSim(broker_ip=args.broker, broker_port=args.port,
                        client_id=args.id, publish_interval=args.interval)
    sim.start()
    print(f"Publishing to {args.broker}:{args.port} — Ctrl+C to stop")
    try:
        while True:
            time.sleep(10)
            print(json.dumps(sim.status(), indent=2))
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()


if __name__ == "__main__":
    main()

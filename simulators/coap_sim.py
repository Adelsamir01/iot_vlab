"""coap_sim.py — Pure-Python CoAP UDP simulator with Zephyr crash semantics.

Mimics zephyr_coap (Zephyr RTOS, Cortex-M3, nanocoap stack). Responds to normal
CoAP GET/POST requests and enters a "crashed" state when a CoAP option overflow
is detected — matching the exploit trigger in apiot/toolkit/ot_exploits.py:coap_option_overflow.

Crash trigger: CoAP option byte 0xDD (delta=13, length=13) — both nibbles indicate
extended 1-byte forms. The real Zephyr nanocoap parser overflows when ext_delta
and ext_length point past the datagram boundary. We detect the 0xDD option byte
as the trigger regardless of ext bytes (matches exact exploit payload).

After crash: stops responding to all UDP datagrams (simulates firmware halt).

Usage:
    python3 coap_sim.py --ip 192.168.100.101 --port 5683
    python3 coap_sim.py --ip 0.0.0.0 --port 56830  # debug port, no root needed
"""

import argparse
import logging
import signal
import socket
import struct
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [coap_sim] %(message)s")
logger = logging.getLogger("coap_sim")

# CoAP constants
COAP_VER = 1
TYPE_CON = 0
TYPE_NON = 1
TYPE_ACK = 2
TYPE_RST = 3

CODE_EMPTY = 0x00
CODE_GET = 0x01
CODE_POST = 0x02
CODE_PUT = 0x03
CODE_DELETE = 0x04

CODE_CREATED = 0x41           # 2.01
CODE_CHANGED = 0x44           # 2.04
CODE_CONTENT = 0x45           # 2.05
CODE_BAD_REQUEST = 0x80       # 4.00
CODE_BAD_OPTION = 0x82        # 4.02
CODE_NOT_FOUND = 0x84         # 4.04
CODE_METHOD_NOT_ALLOWED = 0x85  # 4.05

# The option byte that triggers the crash (matches coap_option_overflow exploit)
CRASH_OPTION_BYTE = 0xDD


def _parse_coap_header(data: bytes) -> dict | None:
    """Parse the 4-byte CoAP fixed header."""
    if len(data) < 4:
        return None
    byte0, code, msg_id = struct.unpack(">BBH", data[:4])
    ver = (byte0 >> 6) & 0x03
    msg_type = (byte0 >> 4) & 0x03
    tkl = byte0 & 0x0F
    return {
        "ver": ver, "type": msg_type, "tkl": tkl,
        "code": code, "msg_id": msg_id,
    }


def _build_coap_response(msg_type: int, code: int, msg_id: int,
                          token: bytes, payload: bytes = b"") -> bytes:
    """Build a CoAP response packet."""
    byte0 = (COAP_VER << 6) | (msg_type << 4) | len(token)
    header = struct.pack(">BBH", byte0, code, msg_id)
    body = header + token
    if payload:
        body += bytes([0xFF]) + payload  # payload marker
    return body


def _detect_crash_trigger(data: bytes, hdr: dict) -> bool:
    """Check if this datagram contains the crash-triggering option."""
    offset = 4 + hdr["tkl"]  # skip fixed header + token
    if offset >= len(data):
        return False
    # Look for 0xDD option byte anywhere in the options section (before 0xFF marker)
    while offset < len(data):
        byte = data[offset]
        if byte == 0xFF:  # payload marker — end of options
            break
        if byte == CRASH_OPTION_BYTE:
            return True
        offset += 1
    return False


def _handle_request(data: bytes, addr: tuple) -> bytes | None:
    """Parse a CoAP request and build a response. Returns None to drop silently."""
    hdr = _parse_coap_header(data)
    if hdr is None or hdr["ver"] != 1:
        # Silently drop packets with invalid CoAP version (not parseable)
        return None

    # Token length > 8 is a protocol error per RFC 7252 §3
    if hdr["tkl"] > 8:
        return _build_coap_response(TYPE_RST, CODE_BAD_REQUEST, hdr["msg_id"], b"")

    token = data[4: 4 + hdr["tkl"]] if hdr["tkl"] else b""
    resp_type = TYPE_ACK if hdr["type"] == TYPE_CON else TYPE_NON

    code = hdr["code"]

    if code == CODE_GET:
        # Respond with a simple sensor reading payload
        payload = b'{"temp":22,"humidity":55,"status":"ok"}'
        return _build_coap_response(resp_type, CODE_CONTENT, hdr["msg_id"], token, payload)

    elif code == CODE_POST:
        # Acknowledge POST (e.g. actuator command accepted)
        return _build_coap_response(resp_type, CODE_CHANGED, hdr["msg_id"], token)

    elif code == CODE_PUT:
        return _build_coap_response(resp_type, CODE_CREATED, hdr["msg_id"], token)

    elif code == CODE_DELETE:
        # DELETE not supported on this resource
        return _build_coap_response(resp_type, CODE_METHOD_NOT_ALLOWED, hdr["msg_id"], token)

    elif code == CODE_EMPTY:
        # Ping / empty CON → ACK
        return _build_coap_response(TYPE_ACK, CODE_EMPTY, hdr["msg_id"], b"")

    # Unknown code — return 4.05 Method Not Allowed
    return _build_coap_response(resp_type, CODE_METHOD_NOT_ALLOWED, hdr["msg_id"], token)


class CoAPSim:
    """CoAP UDP device simulator."""

    WATCHDOG_TIMEOUT_S = 60  # seconds before auto-reset (simulates embedded watchdog)

    def __init__(self, ip: str = "0.0.0.0", port: int = 5683,
                 watchdog_timeout: float = 60):
        self.ip = ip
        self.port = port
        self._crashed = threading.Event()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_timeout = watchdog_timeout
        self.start_time = time.time()

    @property
    def crashed(self) -> bool:
        return self._crashed.is_set()

    def reset(self):
        """Reset crash state (simulates watchdog reboot)."""
        self._crashed.clear()
        logger.info("Device reset — crash state cleared.")

    def _watchdog(self):
        """Auto-reset after watchdog_timeout seconds in crashed state (realistic MCU behaviour)."""
        while not self._stop.is_set():
            if self._crashed.wait(timeout=1.0):
                deadline = time.time() + self._watchdog_timeout
                while time.time() < deadline:
                    if self._stop.is_set():
                        return
                    time.sleep(0.5)
                if self._crashed.is_set() and not self._stop.is_set():
                    logger.info("Watchdog triggered — rebooting (crash state cleared).")
                    self._crashed.clear()

    def start(self):
        """Start the UDP server in a background thread."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.ip, self.port))
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._serve, daemon=True, name="coap-sim")
        self._thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True,
                                                  name="coap-watchdog")
        self._watchdog_thread.start()
        logger.info("CoAP UDP simulator listening on %s:%d", self.ip, self.port)

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if self._crashed.is_set():
                # Drop all packets — simulates firmware halt
                continue

            hdr = _parse_coap_header(data)
            if hdr and _detect_crash_trigger(data, hdr):
                logger.warning(
                    "CoAP option overflow from %s:%d — option byte 0xDD detected. CRASHING.",
                    addr[0], addr[1],
                )
                self._crashed.set()
                # Do NOT send a response — crash is silent
                continue

            resp = _handle_request(data, addr)
            if resp:
                try:
                    self._sock.sendto(resp, addr)
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("CoAP UDP simulator stopped.")


def main():
    parser = argparse.ArgumentParser(description="CoAP UDP simulator (Zephyr crash semantics)")
    parser.add_argument("--ip", default="0.0.0.0", help="Bind IP (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5683, help="Bind port (default: 5683)")
    args = parser.parse_args()

    sim = CoAPSim(ip=args.ip, port=args.port)
    sim.start()

    def _shutdown(sig, frame):
        sim.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Press Ctrl+C to stop.")
    while True:
        time.sleep(1)
        if sim.crashed:
            logger.info("[CRASHED] CoAP simulator in crashed state — dropping all packets.")


if __name__ == "__main__":
    main()

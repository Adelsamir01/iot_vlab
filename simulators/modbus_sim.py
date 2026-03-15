"""modbus_sim.py — Pure-Python Modbus TCP simulator with Zephyr crash semantics.

Mimics arm_modbus_sim (Zephyr RTOS, Cortex-M3, LwIP stack). Responds to normal
Modbus requests and enters a "crashed" state when an MBAP overflow is detected —
matching the exploit trigger in apiot/toolkit/ot_exploits.py:modbus_mbap_overflow.

Crash trigger: MBAP header where claimed_length >= CRASH_LENGTH_THRESHOLD.
After crash: closes the TCP socket and refuses new connections (simulates
firmware halt before watchdog fires).

Usage:
    python3 modbus_sim.py --ip 192.168.100.100 --port 502
    python3 modbus_sim.py --ip 0.0.0.0 --port 5020  # debug port, no root needed
"""

import argparse
import logging
import signal
import socket
import struct
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [modbus_sim] %(message)s")
logger = logging.getLogger("modbus_sim")

# MBAP claimed-length that triggers crash (must match modbus_mbap_overflow exploit)
CRASH_LENGTH_THRESHOLD = 1000

# Simulated coil + holding-register state
_COILS = bytearray(256)
_REGISTERS = bytearray(512)  # 256 registers × 2 bytes each


def _build_response(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    length = len(pdu) + 1  # PDU + unit_id byte
    header = struct.pack(">HHHB", transaction_id, 0x0000, length, unit_id)
    return header + pdu


def _handle_pdu(pdu: bytes) -> bytes:
    """Dispatch a Modbus PDU and return the response PDU.

    Exception codes (per Modbus spec):
      0x01 — ILLEGAL FUNCTION
      0x02 — ILLEGAL DATA ADDRESS (address out of range)
      0x03 — ILLEGAL DATA VALUE (e.g. bad count or value)
    """
    if not pdu:
        return bytes([0x81, 0x01])  # illegal function exception

    fc = pdu[0]

    if fc == 0x01:  # Read Coils
        if len(pdu) < 5:
            return bytes([0x81, 0x03])
        addr, count = struct.unpack(">HH", pdu[1:5])
        if count == 0 or count > 2000:
            return bytes([0x81, 0x03])  # illegal data value
        if addr + count > 256:
            return bytes([0x81, 0x02])  # illegal data address
        byte_count = (count + 7) // 8
        coil_bytes = bytes([
            sum((_COILS[addr + i] & 1) << i for i in range(min(8, count - b * 8)))
            for b in range(byte_count)
        ])
        return bytes([0x01, byte_count]) + coil_bytes

    elif fc == 0x02:  # Read Discrete Inputs (mapped to coil space for simplicity)
        if len(pdu) < 5:
            return bytes([0x82, 0x03])
        addr, count = struct.unpack(">HH", pdu[1:5])
        if count == 0 or count > 2000:
            return bytes([0x82, 0x03])
        if addr + count > 256:
            return bytes([0x82, 0x02])
        byte_count = (count + 7) // 8
        coil_bytes = bytes([
            sum((_COILS[addr + i] & 1) << i for i in range(min(8, count - b * 8)))
            for b in range(byte_count)
        ])
        return bytes([0x02, byte_count]) + coil_bytes

    elif fc == 0x03:  # Read Holding Registers
        if len(pdu) < 5:
            return bytes([0x83, 0x03])
        addr, count = struct.unpack(">HH", pdu[1:5])
        if count == 0 or count > 125:
            return bytes([0x83, 0x03])  # illegal data value
        if addr + count > 256:
            return bytes([0x83, 0x02])  # illegal data address
        byte_count = count * 2
        data = _REGISTERS[addr * 2: addr * 2 + byte_count]
        return bytes([0x03, byte_count]) + data

    elif fc == 0x04:  # Read Input Registers (mapped to holding register space)
        if len(pdu) < 5:
            return bytes([0x84, 0x03])
        addr, count = struct.unpack(">HH", pdu[1:5])
        if count == 0 or count > 125:
            return bytes([0x84, 0x03])
        if addr + count > 256:
            return bytes([0x84, 0x02])
        byte_count = count * 2
        data = _REGISTERS[addr * 2: addr * 2 + byte_count]
        return bytes([0x04, byte_count]) + data

    elif fc == 0x05:  # Write Single Coil
        if len(pdu) < 5:
            return bytes([0x85, 0x03])
        addr, value = struct.unpack(">HH", pdu[1:5])
        if value not in (0x0000, 0xFF00):
            return bytes([0x85, 0x03])  # illegal data value
        if addr >= 256:
            return bytes([0x85, 0x02])  # illegal data address
        _COILS[addr] = 1 if value == 0xFF00 else 0
        return pdu  # echo back

    elif fc == 0x06:  # Write Single Register
        if len(pdu) < 5:
            return bytes([0x86, 0x03])
        addr, value = struct.unpack(">HH", pdu[1:5])
        if addr >= 256:
            return bytes([0x86, 0x02])  # illegal data address
        idx = addr * 2
        struct.pack_into(">H", _REGISTERS, idx, value)
        return pdu  # echo back

    else:
        return bytes([fc | 0x80, 0x01])  # illegal function


def _handle_connection(conn: socket.socket, addr: tuple, crashed_event: threading.Event):
    """Handle one TCP connection. Closes immediately if already crashed."""
    if crashed_event.is_set():
        conn.close()
        return

    with conn:
        conn.settimeout(30.0)
        try:
            while not crashed_event.is_set():
                # Read MBAP header (7 bytes)
                header_bytes = b""
                while len(header_bytes) < 7:
                    chunk = conn.recv(7 - len(header_bytes))
                    if not chunk:
                        return
                    header_bytes += chunk

                transaction_id, protocol_id, claimed_length, unit_id = struct.unpack(
                    ">HHHB", header_bytes
                )

                # ── CRASH TRIGGER ─────────────────────────────────
                if claimed_length >= CRASH_LENGTH_THRESHOLD:
                    logger.warning(
                        "MBAP overflow from %s:%d — claimed_length=%d (threshold=%d). "
                        "CRASHING.",
                        addr[0], addr[1], claimed_length, CRASH_LENGTH_THRESHOLD,
                    )
                    crashed_event.set()
                    return  # close connection without responding (simulates firmware halt)

                # Read PDU (claimed_length - 1 byte for unit_id already read)
                pdu_len = max(0, claimed_length - 1)
                pdu = b""
                while len(pdu) < pdu_len:
                    chunk = conn.recv(pdu_len - len(pdu))
                    if not chunk:
                        return
                    pdu += chunk

                resp_pdu = _handle_pdu(pdu)
                resp = _build_response(transaction_id, unit_id, resp_pdu)
                conn.sendall(resp)

        except (socket.timeout, ConnectionResetError, BrokenPipeError, OSError):
            pass


class ModbusSim:
    """Modbus TCP device simulator."""

    def __init__(self, ip: str = "0.0.0.0", port: int = 502,
                 watchdog_timeout: float = 10):
        self.ip = ip
        self.port = port
        self._crashed = threading.Event()
        self._stop = threading.Event()
        self._server_sock: socket.socket | None = None
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
        """Start the TCP server in a background thread."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.ip, self.port))
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._serve, daemon=True, name="modbus-sim")
        self._thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True,
                                                  name="modbus-watchdog")
        self._watchdog_thread.start()
        logger.info("Modbus TCP simulator listening on %s:%d", self.ip, self.port)

    def _serve(self):
        while not self._stop.is_set():
            if self._crashed.is_set():
                time.sleep(0.1)
                continue
            try:
                conn, addr = self._server_sock.accept()
                t = threading.Thread(
                    target=_handle_connection,
                    args=(conn, addr, self._crashed),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._stop.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("Modbus TCP simulator stopped.")


def main():
    parser = argparse.ArgumentParser(description="Modbus TCP simulator (Zephyr crash semantics)")
    parser.add_argument("--ip", default="0.0.0.0", help="Bind IP (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=502, help="Bind port (default: 502)")
    args = parser.parse_args()

    sim = ModbusSim(ip=args.ip, port=args.port)
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
            logger.info("[CRASHED] Modbus simulator in crashed state — not accepting connections.")


if __name__ == "__main__":
    main()

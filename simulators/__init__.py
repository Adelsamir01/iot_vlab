"""iot_vlab.simulators — Pure-Python Modbus TCP and CoAP UDP device simulators.

Replaces QEMU Cortex-M3 firmware for multi-device experiments where the
Stellaris MAC constraint prevents running more than one bare-metal MCU.

Each simulator mimics the crash semantics of the real Zephyr firmware:
- Normal operation: responds to valid requests
- Crashed state: stops responding (simulates firmware halt / watchdog reset pending)

Usage (via sim_manager):
    from iot_vlab.simulators.sim_manager import SimManager
    mgr = SimManager()
    ip = mgr.start_modbus()          # returns allocated IP string
    ip2 = mgr.start_coap()
    ...
    mgr.stop_all()
"""

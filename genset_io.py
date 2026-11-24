#!/usr/bin/env python3


import os
import sys
import signal
import json
import threading

from functools import partial
from pymodbus.client.sync import ModbusTcpClient

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------


SERVICE_NAME = "com.victronenergy.switch.genset"
DEVICE_INSTANCE = 0
PRODUCT_NAME = "15kVA Genset IO Controller"
VERSION = "1.0"

# Dingtian Modbus TCP settings

MODBUS_HOST = "192.168.1.100"
MODBUS_PORT = 502
MODBUS_UNIT_ID = 1
MODBUS_POLL_INTERVAL = 0.5
MODBUS_SOCKET_TIMEOUT = 1.0
MODBUS_INPUT_REGISTER_START = 0x0016
MODBUS_RELAY_REGISTER_START = 0x0036

INPUT_GROUP = "Genset Inputs"
RELAY_GROUP = "Genset Relays"

INPUTS = {
    "generator": {
        "name": "Off Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "cabin_lights": {
        "name": "On Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "exterior_lights": {
        "name": "ChargeOnly Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "water_pump": {
        "name": "Off LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "shore_power": {
        "name": "On LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "charger": {
        "name": "ChargeOnly LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "bilge_alarm": {
        "name": "BMS Wake State",
        "initial": 0,
        "labels": ["Inactive", "Active"],
    },
}

RELAYS = {
    "off_button_led": {
        "name": "Off Button LED",
        "initial": 0,
    },
    "on_button_led": {
        "name": "On Button LED",
        "initial": 0,
    },
    "charge_button_led": {
        "name": "ChargeOnly Button LED",
        "initial": 0,
    },
    "bms_wake": {
        "name": "BMS Wake Signal",
        "initial": 0,
    },
    "dse_remote_start": {
        "name": "DSE Remote Start",
        "initial": 0,
    },
    "dse_lock_keypad_mode": {
        "name": "DSE Lock Keypad Mode Request",
        "initial": 0,
    },
    "earth_leakage_reset": {
        "name": "Earth Leakage Reset",
        "initial": 0,
    },
    "reverse_power_detection": {
        "name": "Reverse Power Detection Signal",
        "initial": 0,
    },
}


# --------------------------------------------------
# VICTRON PYTHON LIBRARY
# --------------------------------------------------


for path in [
    "/opt/victronenergy/velib_python",
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
]:
    if os.path.isdir(path):
        sys.path.insert(1, path)
        break
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService


# --------------------------------------------------
# GENSET IO CONTROLLER
# --------------------------------------------------


class GensetIOController:

    def __init__(self):
        DBusGMainLoop(set_as_default=True)

        self.service = None
        self.mainloop = GLib.MainLoop()
        self.stop_event = threading.Event()
        self.modbus_thread = None
        self.modbus_client = None
        self.relay_state_lock = threading.Lock()
        self.desired_relay_states = {relay_id: int(config["initial"]) for relay_id, config in RELAYS.items()}

        self.validate_config()
        self.create_dbus_service()

    # --------------------------------------------------
    # CONFIGURATION
    # --------------------------------------------------

    @staticmethod
    def validate_config():

        for category, devices in (
            ("input", INPUTS),
            ("relay", RELAYS),
        ):
            for device_id, config in devices.items():

                if not device_id.replace("_", "").isalnum():
                    raise ValueError(f"Invalid {category} ID: {device_id}")
                name = config.get("name", "")

                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"Missing name for {device_id}")
                if len(name.encode("utf-8")) > 32:
                    raise ValueError(f"Name too long for {device_id}")
                if config.get("initial") not in (0, 1):
                    raise ValueError(f"Invalid initial state for {device_id}")
                if category == "input":
                    labels = config.get("labels")
                    if not isinstance(labels, list) or len(labels) != 2:
                        raise ValueError(f"Input {device_id} must have exactly two labels")
                    if not all(isinstance(label, str) for label in labels):
                        raise ValueError(f"Input {device_id} labels must be strings")

    # --------------------------------------------------
    # MODBUS INTERFACE
    # --------------------------------------------------

    def modbus_worker(self):
        """Poll the Dingtian module and apply requested relay states."""

        self.modbus_client = ModbusTcpClient(MODBUS_HOST, port=MODBUS_PORT, timeout=MODBUS_SOCKET_TIMEOUT)
        last_sent_states = {}

        print(f"Modbus worker started: {MODBUS_HOST}:{MODBUS_PORT}, " f"unit ID {MODBUS_UNIT_ID}")

        try:
            while not self.stop_event.is_set():
                try:
                    if not self.modbus_client.connect():
                        print(f"Modbus connection failed: " f"{MODBUS_HOST}:{MODBUS_PORT}")
                        self.stop_event.wait(MODBUS_POLL_INTERVAL)
                        continue
                    with self.relay_state_lock:
                        requested_states = dict(self.desired_relay_states)
                    # Configuration order maps directly to Dingtian channels.

                    for channel, (relay_id, state) in enumerate(requested_states.items()):
                        if last_sent_states.get(relay_id) == state:
                            continue
                        register = MODBUS_RELAY_REGISTER_START + channel
                        response = self.modbus_client.write_register(register, state, unit=MODBUS_UNIT_ID)
                        if response.isError():
                            raise RuntimeError(
                                f"Modbus write failed for {relay_id} " f"(register 0x{register:04X}): {response}"
                            )
                        last_sent_states[relay_id] = state
                        print(f"Modbus relay command: {relay_id} = {state} " f"(register 0x{register:04X})")
                    input_response = self.modbus_client.read_holding_registers(
                        MODBUS_INPUT_REGISTER_START, count=len(INPUTS), unit=MODBUS_UNIT_ID
                    )
                    if input_response.isError():
                        raise RuntimeError(f"Modbus input read failed: {input_response}")
                    if len(input_response.registers) != len(INPUTS):
                        raise RuntimeError("Unexpected number of input registers returned")
                    input_states = {
                        input_id: int(value != 0) for input_id, value in zip(INPUTS.keys(), input_response.registers)
                    }

                    relay_response = self.modbus_client.read_holding_registers(
                        MODBUS_RELAY_REGISTER_START, count=len(RELAYS), unit=MODBUS_UNIT_ID
                    )
                    if relay_response.isError():
                        raise RuntimeError(f"Modbus relay read failed: {relay_response}")
                    if len(relay_response.registers) != len(RELAYS):
                        raise RuntimeError("Unexpected number of relay registers returned")
                    relay_feedback = {
                        relay_id: int(value != 0) for relay_id, value in zip(RELAYS.keys(), relay_response.registers)
                    }

                    # All D-Bus updates are executed by the GLib main loop.

                    GLib.idle_add(self.apply_hardware_states, input_states, relay_feedback)
                except Exception as ex:
                    print(f"Modbus communication error: {ex}")
                    last_sent_states.clear()
                    self.modbus_client.close()
                self.stop_event.wait(MODBUS_POLL_INTERVAL)
        finally:
            self.modbus_client.close()
            print("Modbus worker stopped")

    def apply_hardware_states(self, input_states, relay_feedback):
        """Apply hardware values on the GLib main-loop thread."""

        if self.service is None:
            return False
        for input_id, value in input_states.items():
            self.service[f"/GenericInput/{input_id}/Value"] = value
        for relay_id, value in relay_feedback.items():
            self.service[f"/SwitchableOutput/{relay_id}/Status"] = 0x09 if value else 0x00
        return False

    def start_modbus_thread(self):
        self.modbus_thread = threading.Thread(target=self.modbus_worker, name="DingtianModbus", daemon=True)
        self.modbus_thread.start()

    # --------------------------------------------------
    # D-BUS CALLBACKS
    # --------------------------------------------------

    def relay_changed(self, relay_id, path, new_value):
        """
        Called when an external application writes to
        /SwitchableOutput/<id>/State.
        """

        try:
            state = int(new_value)
        except (ValueError, TypeError):
            print(f"Invalid relay command: {relay_id} = {new_value}")
            return False
        if state not in (0, 1):
            print(f"Invalid relay state: {relay_id} = {state}")
            return False
        print(f"Relay command received: {relay_id} = " f"{'ON' if state else 'OFF'}")

        # The Modbus worker sends this requested state to the Dingtian.

        with self.relay_state_lock:
            self.desired_relay_states[relay_id] = state
        return True

    # --------------------------------------------------
    # D-BUS INITIALISATION
    # --------------------------------------------------

    def create_dbus_service(self):

        self.service = VeDbusService(SERVICE_NAME, register=False)

        # Device information

        self.service.add_path("/DeviceInstance", value=DEVICE_INSTANCE)
        self.service.add_path("/ProductName", value=PRODUCT_NAME)
        self.service.add_path("/Connected", value=1)
        self.service.add_path("/Mgmt/Connection", value="Local")
        self.service.add_path("/Mgmt/ProcessName", value=__file__)
        self.service.add_path("/Mgmt/ProcessVersion", value=VERSION)

        # Inputs

        for input_id, config in INPUTS.items():

            base = f"/GenericInput/{input_id}"
            name = config["name"]

            self.service.add_path(base + "/Name", value=name)
            self.service.add_path(base + "/Value", value=config["initial"])
            self.service.add_path(base + "/Status", value=0)
            self.service.add_path(base + "/Settings/CustomName", value=name)
            self.service.add_path(base + "/Settings/Group", value=INPUT_GROUP)
            self.service.add_path(base + "/Settings/Type", value=0)

            # Preserve the existing JSON-encoded Labels representation.

            self.service.add_path(base + "/Settings/Labels", value=json.dumps(config["labels"]))

            self.service.add_path(base + "/Settings/ValidTypes", value=1)
            self.service.add_path(base + "/Settings/ShowUIInput", value=6)

            print(f"Registered input: {name}")
        # Relays

        for relay_id, config in RELAYS.items():

            base = f"/SwitchableOutput/{relay_id}"
            name = config["name"]
            initial = config["initial"]

            self.service.add_path(base + "/Name", value=name)
            self.service.add_path(
                base + "/State", value=initial, writeable=True, onchangecallback=partial(self.relay_changed, relay_id)
            )
            self.service.add_path(base + "/Status", value=0x09 if initial else 0x00)
            self.service.add_path(base + "/Settings/CustomName", value=name)
            self.service.add_path(base + "/Settings/Group", value=RELAY_GROUP)
            self.service.add_path(base + "/Settings/Type", value=1)
            self.service.add_path(base + "/Settings/ValidTypes", value=2)
            self.service.add_path(base + "/Settings/ShowUIControl", value=6)
            self.service.add_path(base + "/Auto", value=0)

            print(f"Registered relay: {name}")
        # Register after all paths have been created.

        self.service.register()

        print(f"D-Bus service registered: {SERVICE_NAME}")

    # --------------------------------------------------
    # APPLICATION
    # --------------------------------------------------

    def shutdown(self, signum=None, frame=None):
        print("Shutdown requested")
        self.stop_event.set()
        self.mainloop.quit()

    def run(self):

        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)

        print(f"Genset IO running: {len(INPUTS)} inputs, " f"{len(RELAYS)} relays")

        self.start_modbus_thread()

        try:
            self.mainloop.run()
        finally:
            self.stop_event.set()
            if self.modbus_thread is not None:
                self.modbus_thread.join(timeout=3.0)
            print("Unregistering D-Bus service")
            if self.service is not None:
                self.service.__del__()
                self.service = None
            print("Shutdown complete")


# --------------------------------------------------
# ENTRY POINT
# --------------------------------------------------


def main():

    controller = GensetIOController()
    controller.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Exception raised")
        print(ex)
        print("Restart required, going down in 5s")
        import time

        time.sleep(5)
        raise

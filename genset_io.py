#!/usr/bin/env python3

import os
import sys
import signal
import json
import threading
from itertools import chain

import dbus

from functools import partial

import psutil
from pymodbus.client.sync import ModbusTcpClient
from pymodbus.transaction import ModbusRtuFramer

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------


SERVICE_NAME = "com.victronenergy.switch.genset"
DEVICE_INSTANCE = 0
PRODUCT_NAME = "15kVA Genset IO Controller"
VERSION = "1.0"

# Dingtian Modbus TCP settings

MODBUS_HOST = "192.169.1.100"
MODBUS_PORT = 502
MODBUS_UNIT_ID = 1
MODBUS_POLL_INTERVAL = 0.25
MODBUS_SOCKET_TIMEOUT = 1.0
MODBUS_INPUT_REGISTER_START = 0x0016
MODBUS_RELAY_REGISTER_START = 0x0036

INPUTS = {
    "off_button": {
        "name": "Off Button",
        "group": "Buttons",
        "labels": ["Released", "Pressed"],
        "show_in_local": False,
        "show_in_vrm": True
    },
    "on_button": {
        "name": "On Button",
        "group": "Buttons",
        "labels": ["Released", "Pressed"],
        "show_in_local": False,
        "show_in_vrm": True
    },
    "charge_button": {
        "name": "ChargeOnly Button",
        "group": "Buttons",
        "labels": ["Released", "Pressed"],
        "show_in_local": False,
        "show_in_vrm": True
    },
    "off_button_led_state": {
        "name": "Off LED State",
        "group": "LEDs",
        "labels": ["Off", "On"],
        "show_in_local": True,
        "show_in_vrm": True
    },
    "on_button_led_state": {
        "name": "On LED State",
        "group": "LEDs",
        "labels": ["Off", "On"],
        "show_in_local": True,
        "show_in_vrm": True
    },
    "charge_button_led_state": {
        "name": "ChargeOnly LED State",
        "group": "LEDs",
        "labels": ["Off", "On"],
        "show_in_local": True,
        "show_in_vrm": True
    },
    "bms_wake_state": {
        "name": "BMS Wake State",
        "group": "BMS",
        "labels": ["Inactive", "Active"],
        "show_in_local": True,
        "show_in_vrm": True
    }
}

DIAGNOSTICS = {
    "cpu_load_avg_1": {
        "name": "CPU Load Average  1m",
        "group": "Diagnostics",
        "show_in_local": True,
        "show_in_vrm": True
    },
    "cpu_load_avg_5": {
        "name": "CPU Load Average  5m",
        "group": "Diagnostics",
        "show_in_local": True,
        "show_in_vrm": True
    },
    "cpu_load_avg_15": {
        "name": "CPU Load Average 15m",
        "group": "Diagnostics",
        "show_in_local": True,
        "show_in_vrm": True
    },
    "memory_used_percent": {
        "name": "RAM Used (%)",
        "group": "Diagnostics",
        "show_in_local": True,
        "show_in_vrm": True
    }
}

RELAYS = {
    "off_button_led": {
        "name": "Off Button LED",
        "group": "LEDs",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "on_button_led": {
        "name": "On Button LED",
        "group": "LEDs",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "charge_button_led": {
        "name": "ChargeOnly Button LED",
        "group": "LEDs",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "bms_wake": {
        "name": "BMS Wake Signal",
        "group": "BMS",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "dse_remote_start": {
        "name": "DSE Remote Start",
        "group": "Genset",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "dse_lock_keypad_mode": {
        "name": "DSE Lock Keypad Mode Request",
        "group": "Genset",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
    },
    "earth_leakage_reset": {
        "name": "Earth Leakage Reset",
        "group": "Genset",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": False
    },
    "reverse_power_detection": {
        "name": "Reverse Power Detection Signal",
        "group": "Genset",
        "show_in_local": False,
        "show_in_vrm": True,
        "disable_control": True
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
        self.desired_relay_states = {}
        self.relay_states_initialised = threading.Event()
        self.service_initialised = threading.Event()
        self.service_initialisation_failed = threading.Event()
        self.service_initialisation_error = None

        self.validate_config()

    # --------------------------------------------------
    # CONFIGURATION
    # --------------------------------------------------

    @staticmethod
    def validate_config():

        for category, devices in (
            ("input", INPUTS),
            ("relay", RELAYS),
            ("diagnostics", DIAGNOSTICS),
        ):
            for device_id, config in devices.items():

                if not device_id.replace("_", "").isalnum():
                    raise ValueError(f"Invalid {category} ID: {device_id}")
                name = config.get("name", "")

                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"Missing name for {device_id}")
                if len(name.encode("utf-8")) > 32:
                    raise ValueError(f"Name too long for {device_id}")
                if category == "input":
                    labels = config.get("labels")

                    if labels is not None:
                        if not isinstance(labels, list):
                            raise ValueError(f"Input {device_id} labels must be a list")
                        if not all(isinstance(label, str) for label in labels):
                            raise ValueError(f"Input {device_id} labels must contain only strings")

                if config.get("show_in_local") is None:
                    raise ValueError(f"Invalid show_in_local value for {device_id}")
                if config.get("show_in_vrm") is None:
                    raise ValueError(f"Invalid show_in_vrm value for {device_id}")

                if category == "relay":
                    if config.get("disable_control") is None:
                        raise ValueError(f"Invalid disable_control value for {device_id}")

                group = config.get("group", "")

                if not isinstance(group, str):
                    raise ValueError(f"Invalid group for {device_id}: must be a string")

                if len(group.encode("utf-8")) > 32:
                    raise ValueError(f"Group name for {device_id} exceeds 32 UTF-8 bytes")
    # --------------------------------------------------
    # MODBUS INTERFACE
    # --------------------------------------------------

    def modbus_worker(self):
        """Poll the Dingtian module and apply requested relay states."""

        self.modbus_client = ModbusTcpClient(
            MODBUS_HOST,
            port=MODBUS_PORT,
            timeout=MODBUS_SOCKET_TIMEOUT,
            framer=ModbusRtuFramer
        )

        connected = False
        connection_error_reported = False

        print(
            f"Modbus worker started: {MODBUS_HOST}:{MODBUS_PORT}, "
            f"unit ID {MODBUS_UNIT_ID}"
        )

        diagnostic_counter = 0

        try:
            while not self.stop_event.is_set():
                diagnostic_counter += 1

                try:
                    if not connected:
                        if not self.modbus_client.connect():
                            raise ConnectionError(
                                f"Unable to connect to {MODBUS_HOST}:{MODBUS_PORT}"
                            )

                        connected = True
                        connection_error_reported = False
                        print("Modbus connected", flush=True)

                        # Do not register D-Bus until valid hardware states are available.
                        if not self.service_initialised.is_set():

                            relay_registers = self.read_modbus_registers(
                                self.modbus_client,
                                MODBUS_RELAY_REGISTER_START,
                                len(RELAYS),
                                "Initial relay read",
                                MODBUS_UNIT_ID
                            )

                            input_registers = self.read_modbus_registers(
                                self.modbus_client,
                                MODBUS_INPUT_REGISTER_START,
                                len(INPUTS),
                                "Initial input read",
                                MODBUS_UNIT_ID
                            )

                            initial_relay_states = {
                                relay_id: int(value != 0)
                                for relay_id, value in zip(RELAYS, relay_registers)
                            }

                            initial_input_states = {
                                input_id: int(value != 0)
                                for input_id, value in zip(INPUTS, input_registers)
                            }

                            print(
                                f"Initial Modbus Relays: {relay_registers}",
                                flush=True
                            )
                            print(
                                f"Initial Modbus Inputs: {input_registers}",
                                flush=True
                            )

                            # Reset the callback result before each attempt.
                            self.service_initialisation_error = None
                            self.service_initialisation_failed.clear()

                            GLib.idle_add(
                                self.create_dbus_service_safe,
                                initial_input_states,
                                initial_relay_states
                            )

                            while not self.service_initialised.is_set():
                                if self.service_initialisation_failed.is_set():
                                    error = self.service_initialisation_error
                                    raise RuntimeError(
                                        f"D-Bus service initialisation failed: {error}"
                                    ) from error
                                if self.stop_event.wait(0.05):
                                    return

                    # ---------------------------------------------------------
                    # Read actual relay states
                    # ---------------------------------------------------------

                    relay_registers = self.read_modbus_registers(
                        self.modbus_client,
                        MODBUS_RELAY_REGISTER_START,
                        len(RELAYS),
                        "Relay read",
                        MODBUS_UNIT_ID
                    )

                    # A successful Modbus transaction confirms the connection.
                    if self.service is not None:
                        GLib.idle_add(self.set_connection_state, 1)

                    reported_states = {
                        relay_id: int(value != 0)
                        for relay_id, value in zip(RELAYS, relay_registers)
                    }

                    print(
                        f"Modbus Relays        : {relay_registers}",
                        flush=True
                    )

                    # Snapshot requested states.
                    with self.relay_state_lock:
                        requested_states = dict(self.desired_relay_states)

                    # ---------------------------------------------------------
                    # Write relays where requested and actual states differ
                    # ---------------------------------------------------------

                    for channel, relay_id in enumerate(RELAYS):
                        requested = requested_states[relay_id]
                        reported = reported_states[relay_id]

                        if requested == reported:
                            continue

                        register = MODBUS_RELAY_REGISTER_START + channel

                        self.write_modbus_register(
                            self.modbus_client,
                            register,
                            requested,
                            f"Relay {relay_id}",
                            MODBUS_UNIT_ID
                        )

                    # ---------------------------------------------------------
                    # Read relay states again for feedback
                    # ---------------------------------------------------------

                    relay_registers = self.read_modbus_registers(
                        self.modbus_client,
                        MODBUS_RELAY_REGISTER_START,
                        len(RELAYS),
                        "Relay feedback read",
                        MODBUS_UNIT_ID
                    )

                    relay_feedback = {
                        relay_id: int(value != 0)
                        for relay_id, value in zip(RELAYS, relay_registers)
                    }

                    print(
                        f"Modbus Relay Feedback: "
                        f"{relay_registers}",
                        flush=True
                    )

                    # Report any relay that did not reach its requested state.
                    for relay_id in RELAYS:
                        if requested_states[relay_id] != relay_feedback[relay_id]:
                            print(
                                f"Modbus WARNING: {relay_id} requested "
                                f"{requested_states[relay_id]}, "
                                f"actual {relay_feedback[relay_id]}",
                                flush=True
                            )

                    # ---------------------------------------------------------
                    # Read physical inputs
                    # ---------------------------------------------------------

                    input_registers = self.read_modbus_registers(
                        self.modbus_client,
                        MODBUS_INPUT_REGISTER_START,
                        len(INPUTS),
                        "Input read",
                        MODBUS_UNIT_ID
                    )

                    input_states = {
                        input_id: int(value != 0)
                        for input_id, value in zip(INPUTS, input_registers)
                    }

                    print(
                        f"Modbus Inputs        : {input_registers}",
                        flush=True
                    )

                    # Update D-Bus from the GLib main-loop thread.
                    GLib.idle_add(
                        self.apply_hardware_states,
                        input_states,
                        relay_feedback
                    )

                    # ---------------------------------------------------------
                    # Diagnostics
                    # ---------------------------------------------------------

                    if diagnostic_counter >= 10:
                        GLib.idle_add(self.populate_diagnostics)
                        diagnostic_counter = 0

                except Exception as ex:
                    if connected or not connection_error_reported:
                        print(f"Modbus communication error: {ex}", flush=True)

                    connection_error_reported = True
                    connected = False
                    self.modbus_client.close()

                    GLib.idle_add(self.set_connection_state, 0)

                self.stop_event.wait(MODBUS_POLL_INTERVAL)

        finally:
            self.modbus_client.close()
            print("Modbus worker stopped", flush=True)

    @staticmethod
    def read_modbus_registers(client, address, count, description, unit):
        """Read and validate a block of Modbus holding registers."""

        response = client.read_holding_registers(
            address,
            count=count,
            unit=unit
        )

        if response is None:
            raise RuntimeError(f"{description}: no Modbus response")

        if response.isError():
            raise RuntimeError(f"{description}: Modbus error: {response}")

        if response.registers is None or len(response.registers) != count:
            actual = len(response.registers) if response.registers else 0
            raise RuntimeError(
                f"{description}: received {actual} registers, expected {count}"
            )

        return response.registers

    @staticmethod
    def write_modbus_register(client, address, value, description, unit):
        """Write and validate a single Modbus holding register."""

        response = client.write_register(
            address,
            value,
            unit=unit
        )

        if response is None:
            raise RuntimeError(
                f"{description}: no Modbus response "
                f"(register 0x{address:04X}, value 0x{value:04X}). "
                "Write outcome is unknown."
            )

        if response.isError():
            raise RuntimeError(
                f"{description}: Modbus write error: {response} "
                f"(register 0x{address:04X}, value 0x{value:04X})"
            )

        print(
            f"Modbus Write OK: {description}, "
            f"register=0x{address:04X}, "
            f"value=0x{value:04X}, response={response}",
            flush=True
        )

        return response

    def apply_hardware_states(self, input_states, relay_feedback):
        """Apply hardware values on the GLib main-loop thread."""

        if self.service is None:
            return False
        for input_id, value in input_states.items():
            self.service[f"/GenericInput/{input_id}/Value"] = value

        for relay_id, value in relay_feedback.items():
            status = 0x09 if value else 0x00

            if RELAYS[relay_id].get("disable_control") == True:
                status |= 0x20

            self.service[f"/SwitchableOutput/{relay_id}/Status"] = status
        return False

    def populate_diagnostics(self):
        """Update diagnostic values on the GLib main-loop thread."""
        if self.service is None:
            return False
        psu_load_avg = psutil.getloadavg()
        cpu_load_avg_1 = round(psu_load_avg[0], 2)
        cpu_load_avg_5 = round(psu_load_avg[1], 2)
        cpu_load_avg_15 = round(psu_load_avg[2], 2)
        memory = psutil.virtual_memory()
        memory_used_percent = round(memory.percent, 1)
        print(f"memory used (%) : {memory_used_percent:.2f}, cpu load avg 1m/5m/15m : {cpu_load_avg_1}/{cpu_load_avg_5}/{cpu_load_avg_15}", flush=True)
        self.service[f'/GenericInput/cpu_load_avg_1/Value'] = cpu_load_avg_1
        self.service[f'/GenericInput/cpu_load_avg_5/Value'] = cpu_load_avg_5
        self.service[f'/GenericInput/cpu_load_avg_15/Value'] = cpu_load_avg_15
        self.service[f'/GenericInput/memory_used_percent/Value'] = memory_used_percent
        return False

    def set_connection_state(self, connected):
        """Update the D-Bus connection state on the GLib main-loop thread."""

        if self.service is not None:
            self.service["/Connected"] = int(bool(connected))

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

        if not self.relay_states_initialised.is_set():
            print(f"Ignoring relay command during initialisation: {relay_id}", flush=True)
            return False

        try:
            state = int(new_value)
        except (ValueError, TypeError):
            print(f"Invalid relay command: {relay_id} = {new_value}", flush=True)
            return False
        if state not in (0, 1):
            print(f"Invalid relay state: {relay_id} = {state}", flush=True)
            return False
        print(f"Relay command received: {relay_id} = " f"{'ON' if state else 'OFF'}", flush=True)

        # The Modbus worker sends this requested state to the Dingtian.

        with self.relay_state_lock:
            self.desired_relay_states[relay_id] = state
        return True

    # --------------------------------------------------
    # D-BUS INITIALISATION
    # --------------------------------------------------

    def create_dbus_service_safe(self, input_states, relay_states):
        """Catch D-Bus setup errors and report them to the Modbus worker."""
        try:
            self.create_dbus_service(input_states, relay_states)
        except Exception as ex:
            self.service_initialisation_error = ex
            self.service_initialisation_failed.set()
            print(f"D-Bus service initialisation failed: {ex}", flush=True)
        return False

    def create_dbus_service(self, input_states, relay_states):
        """Create and register D-Bus using the first valid hardware snapshot."""

        self.service = VeDbusService(SERVICE_NAME, register=False)

        # Adopt the physical relay states before exposing the service.
        with self.relay_state_lock:
            self.desired_relay_states.update(relay_states)

        # Device information

        self.service.add_path("/DeviceInstance", value=DEVICE_INSTANCE)
        self.service.add_path("/ProductName", value=PRODUCT_NAME)
        self.service.add_path("/Connected", value=1)
        self.service.add_path("/Mgmt/Connection", value="Local")
        self.service.add_path("/Mgmt/ProcessName", value=__file__)
        self.service.add_path("/Mgmt/ProcessVersion", value=VERSION)

        # Inputs

        for input_id, config in chain(INPUTS.items(), DIAGNOSTICS.items()):

            base = f"/GenericInput/{input_id}"
            name = config["name"]

            self.service.add_path(base + "/Name", value=name)
            self.service.add_path(
                base + "/Value",
                value=input_states[input_id] if input_id in INPUTS else 0
            )
            self.service.add_path(base + "/Status", value=0)
            self.service.add_path(base + "/Settings/CustomName", value=name)
            self.service.add_path(base + "/Settings/Group", config["group"])
            self.service.add_path(base + "/Settings/Type", value=0)

            # Map each integer input value to its displayed text label.
            # The D-Bus value is a JSON-encoded list of strings.
            self.service.add_path(
                base + "/Settings/Labels",
                value=dbus.Array(
                    [dbus.String(label) for label in config.get("labels", [])],
                    signature="s",
                    variant_level=1
                )
            )
            self.service.add_path(base + "/Settings/ValidTypes", value=1)

            show_ui_control = 0
            if config.get("show_in_local"):
                show_ui_control += 2
            if config.get("show_in_vrm"):
                show_ui_control += 4
            self.service.add_path(base + "/Settings/ShowUIControl", value=show_ui_control)

            print(f"Registered input: {name}")
        # Relays

        for relay_id, config in RELAYS.items():

            base = f"/SwitchableOutput/{relay_id}"
            name = config["name"]
            initial = relay_states[relay_id]

            self.service.add_path(base + "/Name", value=name)
            self.service.add_path(
                base + "/State", value=initial, writeable=True, onchangecallback=partial(self.relay_changed, relay_id)
            )
            status = 0x09 if initial else 0x00
            if config.get("disable_control"):
                status |= 0x20
            self.service.add_path(base + "/Status", value=status)

            self.service.add_path(base + "/Settings/CustomName", value=name)
            self.service.add_path(base + "/Settings/Group", config["group"])
            # self.service.add_path(base + "/Auto", value=0)

            self.service.add_path(base + "/Settings/Type", value=1)
            self.service.add_path(base + "/Settings/ValidTypes", value=2)

            show_ui_control = 0
            if config.get("show_in_local"):
                show_ui_control += 2
            if config.get("show_in_vrm"):
                show_ui_control += 4
            self.service.add_path(base + "/Settings/ShowUIControl", value=show_ui_control)

            print(f"Registered relay: {name}")
        # Register after all paths have been created.

        self.service.register()

        print(f"D-Bus service registered: {SERVICE_NAME}")

        for input_id, value in input_states.items():
            print(f"Initial input state: {input_id} = {value}", flush=True)
        for relay_id, value in relay_states.items():
            print(f"Initial relay state: {relay_id} = {value}", flush=True)

        self.relay_states_initialised.set()
        self.service_initialised.set()

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

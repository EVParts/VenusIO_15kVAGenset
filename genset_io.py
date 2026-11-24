#!/usr/bin/env python3

import os
import sys
import signal
import json

from functools import partial

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SERVICE_NAME = "com.victronenergy.switch.genset"
DEVICE_INSTANCE = 0
PRODUCT_NAME = "15kVA Genset IO Controller"
VERSION = "1.0"

INPUT_GROUP = "Genset Inputs"
RELAY_GROUP = "Genset Relays"

INPUTS = {
    "off_button": {
        "name": "Off Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "on_button": {
        "name": "On Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "charge_button": {
        "name": "ChargeOnly Button",
        "initial": 0,
        "labels": ["Released", "Pressed"],
    },
    "off_button_led_state": {
        "name": "Off LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "on_button_led_state": {
        "name": "On LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "charge_button_led_state": {
        "name": "ChargeOnly LED State",
        "initial": 0,
        "labels": ["Off", "On"],
    },
    "bms_wake_state": {
        "name": "BMS Wake State",
        "initial": 0,
        "labels": ["Inactive", "Active"],
    }
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
                        raise ValueError(
                            f"Input {device_id} must have exactly two labels"
                        )
                    if not all(isinstance(label, str) for label in labels):
                        raise ValueError(
                            f"Input {device_id} labels must be strings"
                        )

    # --------------------------------------------------
    # HARDWARE INTERFACE
    # --------------------------------------------------

    def set_relay(self, relay_id, state):
        """
        Called when a relay is commanded from D-Bus.

        Replace this stub with the hardware control implementation.
        Return True to accept the command or False to reject it.
        """

        print(f"Relay control not yet implemented: {relay_id} = {state}")

        # Accept commands while testing without hardware.
        return True

    def set_input_value(self, input_id, value):
        """
        Update a digital input from the hardware interface.

        Call this whenever an input state changes.
        """

        if input_id not in INPUTS:
            print(f"Unknown input: {input_id}")
            return False

        if value not in (0, 1):
            print(f"Invalid input value: {input_id} = {value}")
            return False

        path = f"/GenericInput/{input_id}/Value"
        self.service[path] = int(value)

        print(f"Input {input_id} = {value}")
        return True

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

        print(
            f"Relay command received: {relay_id} = "
            f"{'ON' if state else 'OFF'}"
        )

        if not self.set_relay(relay_id, state):
            print(f"Relay command rejected: {relay_id}")
            return False

        status_path = f"/SwitchableOutput/{relay_id}/Status"
        self.service[status_path] = 0x09 if state else 0x00

        return True

    # --------------------------------------------------
    # D-BUS INITIALISATION
    # --------------------------------------------------

    def create_dbus_service(self):

        self.service = VeDbusService(
            SERVICE_NAME,
            register=False
        )

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
            self.service.add_path(
                base + "/Value",
                value=config["initial"]
            )
            self.service.add_path(base + "/Status", value=0)
            self.service.add_path(
                base + "/Settings/CustomName",
                value=name
            )
            self.service.add_path(
                base + "/Settings/Group",
                value=INPUT_GROUP
            )
            self.service.add_path(
                base + "/Settings/Type",
                value=0
            )

            # Preserve the existing JSON-encoded Labels representation.
            self.service.add_path(
                base + "/Settings/Labels",
                value=json.dumps(config["labels"])
            )

            self.service.add_path(
                base + "/Settings/ValidTypes",
                value=1
            )
            self.service.add_path(
                base + "/Settings/ShowUIInput",
                value=6
            )

            print(f"Registered input: {name}")

        # Relays
        for relay_id, config in RELAYS.items():

            base = f"/SwitchableOutput/{relay_id}"
            name = config["name"]
            initial = config["initial"]

            self.service.add_path(base + "/Name", value=name)
            self.service.add_path(
                base + "/State",
                value=initial,
                writeable=True,
                onchangecallback=partial(
                    self.relay_changed,
                    relay_id
                )
            )
            self.service.add_path(
                base + "/Status",
                value=0x09 if initial else 0x00
            )
            self.service.add_path(
                base + "/Settings/CustomName",
                value=name
            )
            self.service.add_path(
                base + "/Settings/Group",
                value=RELAY_GROUP
            )
            self.service.add_path(
                base + "/Settings/Type",
                value=1
            )
            self.service.add_path(
                base + "/Settings/ValidTypes",
                value=2
            )
            self.service.add_path(
                base + "/Settings/ShowUIControl",
                value=6
            )
            self.service.add_path(
                base + "/Auto",
                value=0
            )

            print(f"Registered relay: {name}")

        # Register after all paths have been created.
        self.service.register()

        print(f"D-Bus service registered: {SERVICE_NAME}")

    # --------------------------------------------------
    # APPLICATION
    # --------------------------------------------------

    def shutdown(self, signum=None, frame=None):
        print("Shutdown requested")
        self.mainloop.quit()

    def run(self):

        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)

        print(
            f"Genset IO running: {len(INPUTS)} inputs, "
            f"{len(RELAYS)} relays"
        )

        try:
            self.mainloop.run()

        finally:
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

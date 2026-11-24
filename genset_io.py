
#!/usr/bin/env python3

import os
import sys
import signal
import logging

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

# Digital inputs
#
# id       = D-Bus channel identifier
# name     = Display name
# initial  = Initial input state (0 or 1)

INPUTS = {
    "generator": {
        "name": "Off Button",
        "initial": 0,
    },
    "cabin_lights": {
        "name": "On Button",
        "initial": 0,
    },
    "exterior_lights": {
        "name": "ChargeOnly Button",
        "initial": 0,
    },
    "water_pump": {
        "name": "Off LED State",
        "initial": 0,
    },
    "shore_power": {
        "name": "On LED State",
        "initial": 0,
    },
    "charger": {
        "name": "ChargeOnly LED State",
        "initial": 0,
    },
    "bilge_alarm": {
        "name": "BMS Wake State",
        "initial": 0,
    }
}


# Relay outputs
#
# id       = D-Bus channel identifier
# name     = Display name
# initial  = Initial relay state (0 or 1)

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
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

log = logging.getLogger("vehicle_io")


# --------------------------------------------------
# VICRON PYTHON LIBRARY
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
# HARDWARE INTERFACE STUBS
# --------------------------------------------------

def set_relay(relay_id, state):
    """
    Called when a relay is commanded from D-Bus.

    Replace this function with the actual hardware
    control implementation.

    Return True to accept the command.
    Return False to reject the command.
    """

    log.warning(
        "Relay control not yet implemented: %s = %d",
        relay_id,
        state
    )

    # Accept command for virtual testing.
    return True


def set_input_value(input_id, value):
    """
    Update a digital input from the hardware interface.

    Call this whenever an input state changes.
    """

    if input_id not in INPUTS:
        log.error("Unknown input: %s", input_id)
        return False

    if value not in (0, 1):
        log.error("Invalid input value: %s = %s", input_id, value)
        return False

    path = f"/GenericInput/{input_id}/Value"

    dbus[path] = int(value)

    log.debug("Input %s = %d", input_id, value)

    return True


# --------------------------------------------------
# RELAY CALLBACK
# --------------------------------------------------

def relay_changed(relay_id, path, new_value):
    """
    Called when an external application writes
    to /SwitchableOutput/<id>/State.
    """

    try:
        state = int(new_value)
    except (ValueError, TypeError):
        log.error("Invalid relay command: %s = %s", relay_id, new_value)
        return False

    if state not in (0, 1):
        log.error("Invalid relay state: %s = %s", relay_id, state)
        return False

    log.info(
        "Relay command received: %s = %s",
        relay_id,
        "ON" if state else "OFF"
    )

    # Pass command to hardware interface
    if not set_relay(relay_id, state):
        log.error("Relay command rejected: %s", relay_id)
        return False

    # Update reported status
    status_path = f"/SwitchableOutput/{relay_id}/Status"

    dbus[status_path] = 0x09 if state else 0x00

    return True


# --------------------------------------------------
# CONFIGURATION VALIDATION
# --------------------------------------------------

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


# --------------------------------------------------
# D-BUS INITIALISATION
# --------------------------------------------------

def create_dbus_service():

    service = VeDbusService(
        SERVICE_NAME,
        register=False
    )

    # Device information
    service.add_path("/DeviceInstance", value=DEVICE_INSTANCE)
    service.add_path("/ProductName", value=PRODUCT_NAME)
    service.add_path("/Connected", value=1)

    service.add_path(
        "/Mgmt/Connection",
        value="Local"
    )

    service.add_path(
        "/Mgmt/ProcessName",
        value=__file__
    )

    service.add_path(
        "/Mgmt/ProcessVersion",
        value=VERSION
    )

    # --------------------------------------------------
    # INPUTS
    # --------------------------------------------------

    for input_id, config in INPUTS.items():

        base = f"/GenericInput/{input_id}"

        name = config["name"]
        initial = config["initial"]

        service.add_path(
            base + "/Name",
            value=name
        )

        service.add_path(
            base + "/Value",
            value=initial
        )

        service.add_path(
            base + "/Status",
            value=0
        )

        service.add_path(
            base + "/Settings/CustomName",
            value=name
        )

        service.add_path(
            base + "/Settings/Group",
            value=INPUT_GROUP
        )

        service.add_path(
            base + "/Settings/Type",
            value=0
        )

        # Type 0 = discrete indicator
        service.add_path(
            base + "/Settings/ValidTypes",
            value=1
        )

        service.add_path(
            base + "/Settings/ShowUIInput",
            value=6
        )

        log.info("Registered input: %s", name)

    # --------------------------------------------------
    # RELAYS
    # --------------------------------------------------

    for relay_id, config in RELAYS.items():

        base = f"/SwitchableOutput/{relay_id}"

        name = config["name"]
        initial = config["initial"]

        service.add_path(
            base + "/Name",
            value=name
        )

        service.add_path(
            base + "/State",
            value=initial,
            writeable=True,
            onchangecallback=partial(
                relay_changed,
                relay_id
            )
        )

        service.add_path(
            base + "/Status",
            value=0x09 if initial else 0x00
        )

        service.add_path(
            base + "/Settings/CustomName",
            value=name
        )

        service.add_path(
            base + "/Settings/Group",
            value=RELAY_GROUP
        )

        # Type 1 = toggle
        service.add_path(
            base + "/Settings/Type",
            value=1
        )

        # Bit 1 set = toggle supported
        service.add_path(
            base + "/Settings/ValidTypes",
            value=2
        )

        # Local GUI and VRM Remote Console
        service.add_path(
            base + "/Settings/ShowUIControl",
            value=6
        )

        # Manual operation
        service.add_path(
            base + "/Auto",
            value=0
        )

        log.info("Registered relay: %s", name)

    # Register after all paths have been created
    service.register()

    log.info("D-Bus service registered: %s", SERVICE_NAME)

    return service


# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

def main():

    global dbus

    validate_config()

    DBusGMainLoop(set_as_default=True)

    dbus = create_dbus_service()

    mainloop = GLib.MainLoop()

    def shutdown(signum, frame):
        log.info("Shutdown requested")
        mainloop.quit()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info(
        "Vehicle IO running: %d inputs, %d relays",
        len(INPUTS),
        len(RELAYS)
    )

    try:
        mainloop.run()

    finally:
        log.info("Unregistering D-Bus service")
        dbus.__del__()
        log.info("Shutdown complete")


if __name__ == "__main__":
    main()

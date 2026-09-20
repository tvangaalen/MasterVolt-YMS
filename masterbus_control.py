from __future__ import annotations

import argparse
import time

from masterbus_usb import MasterBusUsb
from masterbus_protocol import (
    monitoring_request,
    encode_set_float,
    encode_set_boolean,
    encode_commit,
    decode_monitoring,
)

COMBIMASTER = 0x1B7CE1

CONTROLS = {
    "inverter": {
        "field": 19,
        "name": "Inverter",
        "type": "bool",
        "commit_field": 20,
        "status_field": 47,
        "status_name": "Inverting",
    },
    "charger": {
        "field": 21,
        "name": "Charger",
        "type": "bool",
        "commit_field": 22,
        "status_field": 48,
        "status_name": "Charging",
    },
    "ac-limit": {
        "field": 23,
        "name": "AC IN limit",
        "type": "float",
        "unit": "A",
        "min": 1.0,
        "max": 32.0,
    },
}

STATUS_FIELDS = {
    19: "Inverter enabled",
    21: "Charger enabled",
    23: "AC IN limit",
    47: "Inverting",
    48: "Charging",
    49: "Supporting",
    50: "AC IN present",
    54: "Alarms",
}


def read_field(bus: MasterBusUsb, field: int, timeout_s: float = 1.5):
    bus.drain()

    can_id, data = monitoring_request(COMBIMASTER, field, 0)
    bus.send_frame(can_id, data)

    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        for frame in bus.read_frames(100):
            if frame.address != COMBIMASTER:
                continue

            decoded = decode_monitoring(frame)
            if not decoded:
                continue

            fid, tab, value, _raw = decoded
            if fid == field and tab == 0:
                return value

    raise TimeoutError(f"No read-back for field {field}")


def wait_for_value(
    bus: MasterBusUsb,
    field: int,
    expected: float,
    timeout_s: float = 3.0,
    tolerance: float = 0.05,
):
    deadline = time.monotonic() + timeout_s
    consecutive = 0
    samples = []
    last = None

    while time.monotonic() < deadline:
        try:
            value = read_field(
                bus,
                field,
                timeout_s=min(0.8, max(0.2, deadline - time.monotonic())),
            )
        except TimeoutError:
            time.sleep(0.1)
            continue

        last = value
        samples.append(value)

        if value is not None and abs(value - expected) <= tolerance:
            consecutive += 1
            if consecutive >= 2:
                return True, value, samples
        else:
            consecutive = 0

        time.sleep(0.15)

    return False, last, samples


def write_float(bus: MasterBusUsb, field: int, value: float):
    bus.drain()
    can_id, data = encode_set_float(COMBIMASTER, field, value)
    bus.send_frame(can_id, data)


def write_boolean(bus: MasterBusUsb, field: int, commit_field: int, value: bool):
    """
    CombiMaster relay-style boolean transaction captured from MasterAdjust:

      1. write field as f32 1.0/0.0
      2. write fixed COMMIT_TOKEN to adjacent hidden field

    Example inverter: 19 -> commit field 20
    Example charger : 21 -> commit field 22
    """
    bus.drain()

    can_id, data = encode_set_boolean(COMBIMASTER, field, value)
    bus.send_frame(can_id, data)

    # Keep the two writes close together, but don't merge them.
    time.sleep(0.05)

    can_id, data = encode_commit(COMBIMASTER, commit_field)
    bus.send_frame(can_id, data)


def yn(v: float | None) -> str:
    if v is None:
        return "—"
    return "ON" if v >= 0.5 else "OFF"


def show_status(bus: MasterBusUsb):
    print("CombiMaster status")
    print("------------------")

    for field, name in STATUS_FIELDS.items():
        try:
            value = read_field(bus, field)
        except TimeoutError:
            print(f"{field:3d} {name:<20} TIMEOUT")
            continue

        if field in (19, 21, 47, 48, 49, 50):
            display = yn(value)
        elif field == 23:
            display = f"{value:.2f} A"
        else:
            display = f"{value:g}"

        print(f"{field:3d} {name:<20} {display}")


def set_bool(bus: MasterBusUsb, control_name: str, desired: bool):
    meta = CONTROLS[control_name]
    field = meta["field"]
    commit_field = meta["commit_field"]

    original = read_field(bus, field)
    print(f"Current  : {meta['name']} = {yn(original)}")

    target = 1.0 if desired else 0.0

    if abs(original - target) <= 0.05:
        print("Result   : already at requested state; no write needed.")
        return

    print(f"Writing  : {meta['name']} -> {'ON' if desired else 'OFF'}")
    write_boolean(bus, field, commit_field, desired)
    time.sleep(0.35)

    ok, actual, samples = wait_for_value(bus, field, target, timeout_s=4.0)
    print("Readbacks: " + ", ".join(yn(v) for v in samples))

    if not ok:
        raise SystemExit(
            f"VERIFY FAILED: {meta['name']} expected "
            f"{'ON' if desired else 'OFF'}, last={yn(actual)}"
        )

    print(f"Verify   : OK ({yn(actual)})")

    # Actual operating-state field is informative only: charger may be enabled
    # without charging if AC input is absent, and inverter may be enabled while
    # not presently inverting depending on AC/system state.
    status_field = meta.get("status_field")
    if status_field is not None:
        try:
            time.sleep(0.25)
            s = read_field(bus, status_field)
            print(f"Runtime  : {meta['status_name']} = {yn(s)}")
        except TimeoutError:
            print(f"Runtime  : {meta['status_name']} = timeout")


def set_ac_limit(bus: MasterBusUsb, amps: float):
    meta = CONTROLS["ac-limit"]

    if not (meta["min"] <= amps <= meta["max"]):
        raise SystemExit(
            f"Refusing {amps}: allowed test range is "
            f"{meta['min']}..{meta['max']} A"
        )

    original = read_field(bus, meta["field"])
    print(f"Current  : {original:.3f} A")

    if abs(original - amps) <= 0.05:
        print("Result   : already at requested limit; no write needed.")
        return

    print(f"Writing  : {amps:.3f} A")
    write_float(bus, meta["field"], amps)
    time.sleep(0.35)

    ok, actual, samples = wait_for_value(
        bus, meta["field"], amps, timeout_s=4.0
    )
    print("Readbacks: " + ", ".join(f"{v:.3f}" for v in samples))

    if not ok:
        raise SystemExit(
            f"VERIFY FAILED: expected {amps:.3f} A, last={actual:.3f} A"
        )

    print(f"Verify   : OK ({actual:.3f} A)")


def main():
    parser = argparse.ArgumentParser(
        description="MasterBus CombiMaster controls v0.5"
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    p_inv = sub.add_parser("inverter")
    p_inv.add_argument("state", choices=["on", "off"])
    p_inv.add_argument("--confirm-write", action="store_true")

    p_chg = sub.add_parser("charger")
    p_chg.add_argument("state", choices=["on", "off"])
    p_chg.add_argument("--confirm-write", action="store_true")

    p_lim = sub.add_parser("ac-limit")
    p_lim.add_argument("amps", type=float)
    p_lim.add_argument("--confirm-write", action="store_true")

    args = parser.parse_args()

    with MasterBusUsb() as bus:
        print(f"USB Link : {bus.device_info.get('product_string')}")
        print(f"Device   : CombiMaster {COMBIMASTER:06X}\n")

        if args.command is None or args.command == "status":
            show_status(bus)
            return

        if args.command == "inverter":
            desired = args.state == "on"
            if not args.confirm_write:
                current = read_field(bus, 19)
                print(f"DRY RUN  : Inverter currently {yn(current)}")
                print(f"Would set: Inverter -> {args.state.upper()}")
                print("No write sent.")
                return
            set_bool(bus, "inverter", desired)
            return

        if args.command == "charger":
            desired = args.state == "on"
            if not args.confirm_write:
                current = read_field(bus, 21)
                print(f"DRY RUN  : Charger currently {yn(current)}")
                print(f"Would set: Charger -> {args.state.upper()}")
                print("No write sent.")
                return
            set_bool(bus, "charger", desired)
            return

        if args.command == "ac-limit":
            if not args.confirm_write:
                current = read_field(bus, 23)
                print(f"DRY RUN  : AC IN limit currently {current:.3f} A")
                print(f"Would set: AC IN limit -> {args.amps:.3f} A")
                print("No write sent.")
                return
            set_ac_limit(bus, args.amps)
            return


if __name__ == "__main__":
    main()

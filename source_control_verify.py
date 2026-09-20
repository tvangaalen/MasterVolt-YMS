import argparse
import time

from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_protocol import encode_set_boolean
from masterbus_registry import SOLAR, ALTERNATOR, DEVICE_INFO


def fmt(v):
    return "—" if v is None else f"{v:.6g}"


def inspect_device(service, cd, addr):
    info = DEVICE_INFO[addr]
    print("=" * 88)
    print(f"{info['name']} [{addr:06X}]")
    print("=" * 88)

    fields = cd.fields(addr, info["max_index"])
    rw = [f for f in fields if f.get("writable")]

    if not rw:
        print("No writable fields discovered.")
        print()
        return

    for f in rw:
        idx = int(f["index"])
        try:
            value = service.read_field(addr, idx, .8)
        except Exception:
            value = None

        print(
            f"{idx:>3}  {str(f.get('viz','')):<13} "
            f"value={fmt(value):<12} name={f.get('name','')!r}"
        )

        # For dropdowns, also print the actual option labels returned by MasterBus.
        if str(f.get("viz", "")).lower() == "dropdown":
            try:
                options = cd.list_options(addr, idx, f.get("protocol", "btm1"))
            except Exception as exc:
                options = []
                print(f"     options: error: {exc}")
            if options:
                text = ", ".join(
                    f"{o.get('index')}={o.get('label')!r}" for o in options
                )
                print(f"     options: {text}")

    print()


def solar_reversible_test(service, confirm):
    addr = SOLAR
    field = 12

    original = service.read_field(addr, field, .8)
    target = 0.0 if float(original) >= .5 else 1.0

    def label(v):
        return "ON" if float(v) >= .5 else "OFF"

    print("=" * 88)
    print("SCM Solar reversible ON/OFF verification")
    print("=" * 88)
    print("Field    : 12  On/Off")
    print(f"Original : {label(original)} ({original:g})")
    print(f"Test     : {label(target)} ({target:g})")
    print()

    if not confirm:
        print("DRY RUN ONLY. No write sent.")
        print("Re-run with --solar-test --confirm-write to toggle and automatically restore.")
        return

    # Deliberately bypass set_device_control(): this field is not marked verified yet.
    print(f"Writing  : {label(target)}")
    with service.io_lock:
        service.bus.drain()
        cid, payload = encode_set_boolean(addr, field, target >= .5)
        service.bus.send_frame(cid, payload)

    time.sleep(.6)
    actual = service.read_field(addr, field, .8)
    print(f"Readback : {label(actual)} ({actual:g})")
    if abs(actual - target) > .05:
        raise RuntimeError(f"Solar target verify failed: expected {target}, got {actual}")

    print()
    print(f"Restoring: {label(original)}")
    with service.io_lock:
        service.bus.drain()
        cid, payload = encode_set_boolean(addr, field, original >= .5)
        service.bus.send_frame(cid, payload)

    time.sleep(.6)
    restored = service.read_field(addr, field, .8)
    print(f"Readback : {label(restored)} ({restored:g})")
    if abs(restored - original) > .05:
        raise RuntimeError(f"Solar restore verify failed: expected {original}, got {restored}")

    print("Restore  : OK")
    print()
    print("If the physical/controller state also followed OFF -> ON (or ON -> OFF),")
    print("field 12 is verified for the dashboard control.")


def main():
    ap = argparse.ArgumentParser(
        description="Inspect/verify Solar and Alpha Pro ON/OFF controls"
    )
    ap.add_argument(
        "--inspect",
        action="store_true",
        help="read-only: list writable fields/options for Solar and Alpha Pro",
    )
    ap.add_argument(
        "--solar-test",
        action="store_true",
        help="prepare/test SCM Solar field 12 On/Off",
    )
    ap.add_argument(
        "--confirm-write",
        action="store_true",
        help="required before the reversible Solar write test is performed",
    )
    args = ap.parse_args()

    if not args.inspect and not args.solar_test:
        args.inspect = True

    service = MasterBusService()
    service.open()
    try:
        if args.inspect:
            cd = ControlDiscovery(service)
            print("MasterBus source-control verification")
            print("READ-ONLY inspection; no writes are issued.")
            print()
            inspect_device(service, cd, SOLAR)
            inspect_device(service, cd, ALTERNATOR)
            print("No writes were issued.")
            print("Paste the complete output back into ChatGPT.")

        if args.solar_test:
            solar_reversible_test(service, args.confirm_write)
    finally:
        service.close()


if __name__ == "__main__":
    main()

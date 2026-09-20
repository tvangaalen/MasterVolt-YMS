import argparse
import time

from masterbus_service import MasterBusService

TARGETS = {
    "start": "charger_start",
    "bow": "charger_bow",
}

def yn(v):
    if v is None:
        return "—"
    return "ON" if float(v) >= 0.5 else "STANDBY"

def main():
    parser = argparse.ArgumentParser(
        description="Controlled Mass Charger On/Standby test"
    )
    parser.add_argument("charger", choices=["start", "bow"])
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="required to perform the reversible write test",
    )
    args = parser.parse_args()

    key = TARGETS[args.charger]
    service = MasterBusService()
    service.open()

    try:
        cfg = service.control_maps[key]
        addr = int(cfg["address"], 16)
        field = int(cfg["field"])

        original = service.read_field(addr, field)
        target = 0.0 if original >= 0.5 else 1.0

        print(f"Device   : {key}")
        print(f"Field    : {field} {cfg['name']}")
        print(f"Original : {yn(original)} ({original:g})")
        print(f"Test     : {yn(target)} ({target:g})")
        print()

        if not args.confirm_write:
            print("DRY RUN ONLY. No write sent.")
            print("Re-run with --confirm-write to test and automatically restore.")
            return

        print(f"Writing  : {yn(target)}")
        result = service.set_device_control(key, target >= 0.5)
        print(f"Result   : {result}")
        time.sleep(.5)

        actual = service.read_field(addr, field)
        print(f"Readback : {yn(actual)} ({actual:g})")

        if abs(actual - target) > .05:
            raise RuntimeError("Target verify failed")

        print()
        print(f"Restoring: {yn(original)}")
        result = service.set_device_control(key, original >= 0.5)
        print(f"Result   : {result}")
        time.sleep(.5)

        restored = service.read_field(addr, field)
        print(f"Readback : {yn(restored)} ({restored:g})")

        if abs(restored - original) > .05:
            raise RuntimeError("Restore verify failed")

        print("Restore  : OK")
    finally:
        service.close()

if __name__ == "__main__":
    main()

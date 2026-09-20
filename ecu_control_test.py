import argparse
import time

from masterbus_service import MasterBusService

KEY = "engine_ecu"

def state_name(raw, cfg):
    if raw is None:
        return "—"

    if abs(float(raw) - float(cfg["on_index"])) <= 0.05:
        return "ON"

    if abs(float(raw) - float(cfg["off_index"])) <= 0.05:
        return "OFF"

    return f"UNKNOWN({raw:g})"

def main():
    parser = argparse.ArgumentParser(
        description="Reversible Yanmar ECU Power field test"
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="required to perform the reversible write test",
    )
    args = parser.parse_args()

    service = MasterBusService()
    service.open()

    try:
        cfg = service.control_maps[KEY]
        addr = int(cfg["address"], 16)
        field = int(cfg["field"])

        original = service.read_field(addr, field, .8)

        if abs(original - cfg["on_index"]) <= 0.05:
            target_enabled = False
            target_raw = float(cfg["off_index"])
        elif abs(original - cfg["off_index"]) <= 0.05:
            target_enabled = True
            target_raw = float(cfg["on_index"])
        else:
            raise RuntimeError(
                f"Current ECU Power value {original} is not a known option."
            )

        print("Device   : Engine ECU")
        print(f"Field    : {field} {cfg['name']}")
        print(f"Original : {state_name(original, cfg)} ({original:g})")
        print(f"Test     : {'ON' if target_enabled else 'OFF'} ({target_raw:g})")
        print()

        if not args.confirm_write:
            print("DRY RUN ONLY. No write sent.")
            print("Re-run with --confirm-write to test and automatically restore.")
            return

        print(f"Writing  : {'ON' if target_enabled else 'OFF'}")
        result = service.set_device_control(KEY, target_enabled)
        print(f"Result   : {result}")
        time.sleep(.5)

        actual = service.read_field(addr, field, .8)
        print(f"Readback : {state_name(actual, cfg)} ({actual:g})")

        if abs(actual - target_raw) > .05:
            raise RuntimeError("Target verify failed")

        print()
        restore_enabled = abs(original - cfg["on_index"]) <= .05
        print(f"Restoring: {'ON' if restore_enabled else 'OFF'}")
        result = service.set_device_control(KEY, restore_enabled)
        print(f"Result   : {result}")
        time.sleep(.5)

        restored = service.read_field(addr, field, .8)
        print(f"Readback : {state_name(restored, cfg)} ({restored:g})")

        if abs(restored - original) > .05:
            raise RuntimeError("Restore verify failed")

        print("Restore  : OK")

    finally:
        service.close()

if __name__ == "__main__":
    main()

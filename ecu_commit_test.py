import argparse
import time

from masterbus_service import MasterBusService
from masterbus_protocol import encode_set_float, encode_commit
from masterbus_registry import YANMAR

POWER_FIELD = 56
COMMIT_FIELD = 57
OFF = 0.0
ON = 1.0

def label(v):
    if v is None:
        return "—"
    if abs(v - ON) <= .05:
        return "ON"
    if abs(v - OFF) <= .05:
        return "OFF"
    return f"UNKNOWN({v:g})"

def write_power_with_commit(service, value):
    with service.io_lock:
        service.bus.drain()

        can_id, payload = encode_set_float(
            YANMAR,
            POWER_FIELD,
            value,
        )
        service.bus.send_frame(can_id, payload)

        time.sleep(.05)

        can_id, payload = encode_commit(
            YANMAR,
            COMMIT_FIELD,
        )
        service.bus.send_frame(can_id, payload)

def read_stable(service, expected, timeout=4.0):
    deadline = time.monotonic() + timeout
    consecutive = 0
    samples = []

    while time.monotonic() < deadline:
        try:
            value = service.read_field(
                YANMAR,
                POWER_FIELD,
                .8,
            )
        except Exception:
            time.sleep(.1)
            continue

        samples.append(value)

        if abs(value - expected) <= .05:
            consecutive += 1
            if consecutive >= 2:
                return True, value, samples
        else:
            consecutive = 0

        time.sleep(.15)

    return False, (samples[-1] if samples else None), samples

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Yanmar ECU Power test using field 56 plus "
            "hidden commit field 57"
        )
    )
    parser.add_argument(
        "--confirm-write",
        action="store_true",
        help="required to perform the reversible write",
    )
    args = parser.parse_args()

    service = MasterBusService()
    service.open()

    try:
        original = service.read_field(
            YANMAR,
            POWER_FIELD,
            .8,
        )

        if abs(original - ON) <= .05:
            target = OFF
        elif abs(original - OFF) <= .05:
            target = ON
        else:
            raise RuntimeError(
                f"Unexpected current Power value: {original}"
            )

        print("Device       : INT Yanmar ECU")
        print("Power field  : 56 Power")
        print("Commit field : 57 (unnamed writable Float)")
        print(f"Original     : {label(original)} ({original:g})")
        print(f"Test         : {label(target)} ({target:g})")
        print()
        print(
            "Transaction  : write field 56 value, then "
            "commit token to field 57"
        )
        print()

        if not args.confirm_write:
            print("DRY RUN ONLY. No write sent.")
            print(
                "Re-run with --confirm-write to test "
                "and automatically restore."
            )
            return

        print(f"Writing      : {label(target)}")
        write_power_with_commit(service, target)
        time.sleep(.35)

        ok, actual, samples = read_stable(
            service,
            target,
        )
        print(
            "Readbacks    : "
            + ", ".join(label(v) for v in samples)
        )

        if not ok:
            raise RuntimeError(
                f"Target verify failed; last={actual}"
            )

        print(f"Verify       : OK ({label(actual)})")
        print()

        print(f"Restoring    : {label(original)}")
        write_power_with_commit(service, original)
        time.sleep(.35)

        ok, actual, samples = read_stable(
            service,
            original,
        )
        print(
            "Readbacks    : "
            + ", ".join(label(v) for v in samples)
        )

        if not ok:
            raise RuntimeError(
                f"Restore verify failed; last={actual}"
            )

        print(f"Restore      : OK ({label(actual)})")

    finally:
        service.close()

if __name__ == "__main__":
    main()

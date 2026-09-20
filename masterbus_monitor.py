from __future__ import annotations

import argparse
import time
from collections import Counter

from masterbus_usb import MasterBusUsb
from masterbus_protocol import (
    CAN_CLASS_NAMES,
    decode_device_broadcast,
    decode_monitoring_value,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default=None)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    counts = Counter()
    devices = {}
    values = 0
    frames = 0
    deadline = time.monotonic() + args.seconds

    with MasterBusUsb(serial=args.serial) as bus:
        print("Mastervolt MasterBus passive monitor v0.2")
        print("READ ONLY; no CAN frames are transmitted.\n")

        while time.monotonic() < deadline:
            for frame in bus.read_frames(500):
                frames += 1
                counts[frame.can_class] += 1

                d = decode_device_broadcast(frame)
                if d:
                    devices[d.address] = d
                    continue

                v = decode_monitoring_value(frame)
                if v:
                    values += 1
                    print(
                        f"VALUE {v.address:06X} "
                        f"tab={v.tab_index} field={v.field_index:3d} "
                        f"float={v.float_value}"
                    )

    print("\nSummary")
    print(f"Frames : {frames}")
    print(f"Values : {values}")
    print(f"Devices: {len(devices)}")
    for cls, count in sorted(counts.items()):
        print(f"  0x{cls:02X} {CAN_CLASS_NAMES.get(cls, '?'):<18} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

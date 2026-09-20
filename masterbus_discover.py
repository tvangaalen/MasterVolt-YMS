from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys

from masterbus_usb import MasterBusUsb
from masterbus_discovery import MasterBusDiscovery, save_json


def addr_arg(value: str) -> int:
    value = value.strip().lower().removeprefix("0x")
    return int(value, 16)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MasterBus device + schema discovery via Mastervolt USB Link"
    )
    parser.add_argument("--serial", default=None)
    parser.add_argument(
        "--listen",
        type=float,
        default=4.0,
        help="seconds to collect passive device broadcasts (default: 4)",
    )
    parser.add_argument(
        "--device",
        type=addr_arg,
        default=None,
        help="discover one 24-bit device address, e.g. 6DB09B",
    )
    parser.add_argument(
        "--no-fields",
        action="store_true",
        help="only identify devices/properties; skip field metadata",
    )
    parser.add_argument(
        "--max-fields",
        type=int,
        default=None,
        help="limit metadata probe to first N indices for a quick test",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=250,
        help="request timeout in ms (default: 250)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="retries per request (default: 2)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSON output path; default captures/discovery_TIMESTAMP.json",
    )
    args = parser.parse_args()

    print("Mastervolt MasterBus schema discovery v0.2")
    print("READ/DISCOVERY ONLY: no field values are written.\n")

    out_dir = Path("captures")
    out_dir.mkdir(exist_ok=True)
    output = args.output or str(
        out_dir / f"discovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

    with MasterBusUsb(serial=args.serial) as bus:
        info = bus.device_info
        print(f"USB Link: {info.get('product_string')} / {info.get('serial_number')}")
        print(f"Collecting device broadcasts for {args.listen:.1f}s...\n")

        disc = MasterBusDiscovery(
            bus,
            timeout_ms=args.timeout,
            retries=args.retries,
        )
        devices = disc.collect_devices(args.listen)

        if args.device is not None:
            if args.device not in devices:
                print(
                    f"Warning: requested device {args.device:06X} was not seen "
                    f"in broadcasts; probing it anyway."
                )
                from masterbus_discovery import DeviceInfo
                devices = {
                    args.device: DeviceInfo(
                        address=args.device,
                        type_code=-1,
                        instance=-1,
                        event_counter=-1,
                    )
                }
            else:
                devices = {args.device: devices[args.device]}

        if not devices:
            print("No MasterBus devices discovered.")
            return 2

        print(f"Found {len(devices)} device(s).\n")

        document = []

        for n, dev in enumerate(sorted(devices.values(), key=lambda x: x.address), 1):
            print(f"[{n}/{len(devices)}] Device {dev.address:06X}")
            disc.device_properties(dev)

            print(f"  type       : 0x{dev.type_code:02X}" if dev.type_code >= 0 else "  type       : unknown")
            print(f"  instance   : {dev.instance}")
            print(f"  article    : {dev.article}")
            print(f"  serial     : {dev.serial}")
            print(f"  name       : {dev.name}")
            print(f"  revision   : {dev.revision}")
            print(f"  firmware   : {dev.firmware}")
            print(f"  max index  : {dev.field_max_index}")

            fields = []
            if not args.no_fields:
                print("  probing fields...")
                fields = disc.fields(dev, max_fields=args.max_fields)

                for f in fields:
                    label = f.name or f"field {f.index}"
                    unit = f" {f.unit}" if f.unit else ""
                    value = "N/A" if f.current_value is None else f"{f.current_value:g}{unit}"
                    wr = "RW" if f.writable else ("RO" if f.writable is False else "??")
                    print(
                        f"    {f.index:3d}  {wr:2s}  "
                        f"{(f.viz or '?'):<14} "
                        f"{label:<32} = {value}"
                    )

            document.append({
                "device": asdict(dev),
                "fields": [asdict(f) for f in fields],
            })
            print()

        save_json(output, document)
        print(f"Saved: {output}")
        print("\nNo MasterBus field writes were issued.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

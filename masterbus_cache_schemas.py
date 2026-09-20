from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json

from masterbus_usb import MasterBusUsb
from masterbus_discovery import MasterBusDiscovery, DeviceInfo
from masterbus_registry import KNOWN_DEVICES


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a cached schema for the known MasterBus installation"
    )
    parser.add_argument("--timeout", type=int, default=250)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    out_dir = Path("schemas")
    out_dir.mkdir(exist_ok=True)

    print("Building MasterBus schema cache v0.3")
    print("This sends read/discovery requests only.\n")

    with MasterBusUsb() as bus:
        disc = MasterBusDiscovery(bus, timeout_ms=args.timeout, retries=args.retries)

        for n, (address, known) in enumerate(KNOWN_DEVICES.items(), 1):
            print(f"[{n}/{len(KNOWN_DEVICES)}] {known['name']} ({address:06X})")
            dev = DeviceInfo(
                address=address,
                type_code=-1,
                instance=-1,
                event_counter=-1,
                article=known.get("article"),
                serial=known.get("serial"),
                name=known.get("name"),
                revision=known.get("revision"),
                firmware=known.get("firmware"),
                field_max_index=known.get("max_index"),
            )

            fields = disc.fields(dev)
            payload = {
                "cached_at": datetime.now().isoformat(timespec="seconds"),
                "known": known,
                "device": asdict(dev),
                "fields": [asdict(f) for f in fields],
            }

            path = out_dir / f"{address:06X}_{known['article']}.json"
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  {len(fields)} fields -> {path}")

    print("\nSchema cache complete.")
    print("No MasterBus field writes were issued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

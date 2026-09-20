import argparse

from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_registry import DEVICE_INFO

def fmt_value(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)

def resolve_max_index(addr, override):
    if override is not None:
        return override

    info = DEVICE_INFO.get(addr)
    if info:
        return int(info["max_index"])

    return 255

def main():
    ap = argparse.ArgumentParser(description="MasterBus field snapshot v0.15.5")
    ap.add_argument(
        "--device",
        required=True,
        help="6-digit hexadecimal MasterBus address",
    )
    ap.add_argument("--all-fields", action="store_true")
    ap.add_argument(
        "--max-index",
        type=int,
        default=None,
        help="optional explicit maximum field index",
    )
    args = ap.parse_args()

    addr = int(args.device, 16)
    max_index = resolve_max_index(addr, args.max_index)

    if max_index < 0 or max_index > 255:
        raise SystemExit("--max-index must be between 0 and 255")

    info = DEVICE_INFO.get(addr, {})
    device_name = info.get("name", "Unknown device")

    service = MasterBusService()
    service.open()

    try:
        cd = ControlDiscovery(service)

        print("MasterBus snapshot v0.15.5")
        print("=" * 78)
        print(f"Device  : {device_name}")
        print(f"Address : {addr:06X}")
        print(f"Scan    : fields 0..{max_index}")
        print()

        fields = cd.fields(addr, max_index)

        if not fields:
            raise RuntimeError(
                f"No field metadata discovered for {addr:06X}"
            )

        print(f"Fields  : {len(fields)} discovered")
        print()
        print(" IDX  PROTO  VIZ             RW   VALUE              NAME")
        print("-" * 78)

        for field in fields:
            idx = field["index"]
            proto = field["protocol"]
            viz = field.get("viz") or "?"
            rw = "RW" if field.get("writable") else "RO"
            name = field.get("name") or ""

            try:
                value = service.read_field(addr, idx, .45)
                value_text = fmt_value(value)
            except Exception:
                value_text = "—"

            print(
                f"{idx:4d}  {proto:<5}  {viz:<14}  {rw:<2}   "
                f"{value_text:<18} {name}"
            )

        print()
        print("No writes were issued.")

    finally:
        service.close()

if __name__ == "__main__":
    main()

import time
from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_registry import CHARGER_START, CHARGER_BOW, YANMAR, DEVICE_INFO

TARGETS = [
    ("charger_start", CHARGER_START, range(60, 68)),
    ("charger_bow", CHARGER_BOW, range(60, 68)),
    ("engine_ecu", YANMAR, range(54, 59)),
]

def safe_read(service, addr, field):
    try:
        return service.read_field(addr, field, .8)
    except Exception:
        return None

def main():
    service = MasterBusService()
    service.open()
    try:
        cd = ControlDiscovery(service)

        print("MasterBus focused control inspection v0.11.1")
        print("READ-ONLY: no field writes are issued.")
        print()

        for key, addr, indices in TARGETS:
            info = DEVICE_INFO[addr]
            print("=" * 78)
            print(f"{key}: {info['name']} [{addr:06X}]")
            print(f"Inspecting fields {min(indices)}..{max(indices)}")
            print()

            all_fields = cd.fields(addr, info["max_index"])
            by_index = {f["index"]: f for f in all_fields}

            for idx in indices:
                f = by_index.get(idx)
                value = safe_read(service, addr, idx)

                if f is None and value is None:
                    print(f"  {idx:>3}: no metadata / no readable value")
                    continue

                name = "" if f is None else f.get("name", "")
                viz = "" if f is None else str(f.get("viz"))
                writable = "" if f is None else str(f.get("writable"))
                protocol = "" if f is None else f.get("protocol", "")

                value_text = "—" if value is None else f"{value:.6g}"
                print(
                    f"  {idx:>3}  protocol={protocol:<4} "
                    f"viz={viz:<13} writable={writable:<5} "
                    f"value={value_text:<10} name={name!r}"
                )

            print()

        print("=" * 78)
        print("No writes were issued.")
        print()
        print("Please paste this complete output back into ChatGPT.")
    finally:
        service.close()

if __name__ == "__main__":
    main()

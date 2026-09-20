from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_registry import CHARGER_START, CHARGER_BOW, YANMAR, DEVICE_INFO

TARGETS = [
    ("charger_start", CHARGER_START),
    ("charger_bow", CHARGER_BOW),
    ("engine_ecu", YANMAR),
]

def main():
    service = MasterBusService()
    service.open()
    try:
        cd = ControlDiscovery(service)
        for key, addr in TARGETS:
            info = DEVICE_INFO[addr]
            print("=" * 72)
            print(f"{key}: {info['name']} [{addr:06X}]")
            fields = cd.fields(addr, info["max_index"])
            candidates = cd.control_candidates(fields)

            if not candidates:
                print("No safe-looking writable ON/OFF candidate found.")
                continue

            print("Candidates (READ-ONLY discovery; no writes performed):")
            for c in candidates[:10]:
                print(
                    f"  protocol={c['protocol']:<4} field={c['index']:>3} "
                    f"viz={str(c['viz']):<13} writable={c['writable']} "
                    f"score={c['score']:>2} name={c['name']!r}"
                )
    finally:
        service.close()

if __name__ == "__main__":
    main()

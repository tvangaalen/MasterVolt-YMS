from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_registry import YANMAR

def main():
    service = MasterBusService()
    service.open()

    try:
        cd = ControlDiscovery(service)

        print("Yanmar ECU dropdown-option probe")
        print("READ-ONLY: no writes are issued.")
        print()

        for field, label in ((56, "Power"), (58, "Charger")):
            print("=" * 72)
            print(f"Field {field}: {label}")

            try:
                current = service.read_field(YANMAR, field, .8)
            except Exception:
                current = None

            options = cd.list_options(YANMAR, field, "btm1")

            print(f"Current raw value: {current}")
            print("Options:")

            if not options:
                print("  No option strings returned.")
            else:
                for opt in options:
                    marker = ""
                    if current is not None and abs(float(current) - opt["index"]) <= .05:
                        marker = "  <-- CURRENT"

                    print(
                        f"  index={opt['index']:>2} "
                        f"sid={opt['string_id']:>5} "
                        f"label={opt['label']!r}{marker}"
                    )

            print()

        print("=" * 72)
        print("No writes were issued.")
    finally:
        service.close()

if __name__ == "__main__":
    main()

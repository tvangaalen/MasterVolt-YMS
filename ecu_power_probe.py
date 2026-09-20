from masterbus_service import MasterBusService
from masterbus_control_discovery import ControlDiscovery
from masterbus_registry import YANMAR, DEVICE_INFO

def main():
    service = MasterBusService()
    service.open()
    try:
        cd = ControlDiscovery(service)
        info = DEVICE_INFO[YANMAR]
        fields = cd.fields(YANMAR, info["max_index"])

        print("Yanmar ECU Power control probe")
        print("READ-ONLY: no writes are issued.")
        print()

        for f in fields:
            if f["index"] in (54, 55, 56, 57, 58):
                try:
                    value = service.read_field(YANMAR, f["index"], .8)
                except Exception:
                    value = None
                print(
                    f"field={f['index']:>2} protocol={f['protocol']} "
                    f"viz={f['viz']} writable={f['writable']} "
                    f"value={value} name={f['name']!r}"
                )

        print()
        print("Field 56 is still not enabled for writes until dropdown option semantics are known.")
    finally:
        service.close()

if __name__ == "__main__":
    main()

import time
from masterbus_service import MasterBusService
from masterbus_registry import ALTERNATOR

FIELDS = {
    5: "Charger state",
    39: "Stop charge",
    40: "Stop charge companion",
}

def main():
    svc = MasterBusService()
    svc.open()
    try:
        print("Alpha Pro passive control watcher")
        print("No writes are issued.")
        print("Change 'Stop charge' once in MasterView and watch for changes.")
        print("Press Ctrl+C to stop.\n")
        last = {}
        while True:
            changed = []
            for idx, name in FIELDS.items():
                try:
                    v = svc.read_field(ALTERNATOR, idx, .7)
                except Exception:
                    v = None
                if last.get(idx, object()) != v:
                    changed.append((idx, name, v))
                    last[idx] = v
            if changed:
                stamp = time.strftime("%H:%M:%S")
                for idx, name, v in changed:
                    print(f"{stamp}  field {idx:>2}  {name:<22} = {v}")
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\nStopped. No writes were issued.")
    finally:
        svc.close()

if __name__ == "__main__":
    main()

import argparse, time
from masterbus_service import MasterBusService
from masterbus_registry import YANMAR

FIELD=43

def label(v):
    if v is None:return "—"
    return "ON" if float(v)>=.5 else "OFF"

def main():
    ap=argparse.ArgumentParser(description="Reversible captured MasterAdjust ECU power test (field 43)")
    ap.add_argument("--confirm-write",action="store_true")
    a=ap.parse_args()

    s=MasterBusService();s.open()
    try:
        original=s.read_field(YANMAR,FIELD,.8)
        target=0.0 if original>=.5 else 1.0
        print("Device   : INT Yanmar ECU")
        print("Field    : 43 Mac/Magic On")
        print(f"Original : {label(original)} ({original:g})")
        print(f"Test     : {label(target)} ({target:g})")
        print("Method   : exact field/value semantics observed in MasterAdjust USB capture")
        print()
        if not a.confirm_write:
            print("DRY RUN ONLY. No write sent.")
            print("Re-run with --confirm-write to test and automatically restore.")
            return

        print(f"Writing  : {label(target)}")
        r=s.set_device_control("engine_ecu",target>=.5)
        print("Result   :",r)
        time.sleep(.5)
        actual=s.read_field(YANMAR,FIELD,.8)
        print(f"Readback : {label(actual)} ({actual:g})")
        if abs(actual-target)>.05:raise RuntimeError("Target verify failed")

        print()
        print(f"Restoring: {label(original)}")
        r=s.set_device_control("engine_ecu",original>=.5)
        print("Result   :",r)
        time.sleep(.5)
        restored=s.read_field(YANMAR,FIELD,.8)
        print(f"Readback : {label(restored)} ({restored:g})")
        if abs(restored-original)>.05:raise RuntimeError("Restore verify failed")
        print("Restore  : OK")
    finally:s.close()

if __name__=="__main__":main()

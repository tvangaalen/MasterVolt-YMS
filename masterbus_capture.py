import hid
import time
from datetime import datetime

VID = 0x1A64
PID = 0x0000

devices = hid.enumerate(VID, PID)

if not devices:
    raise SystemExit("MasterBus USB Link not found")

dinfo = devices[0]

print("Opening:")
print(" Product :", dinfo.get("product_string"))
print(" Serial  :", dinfo.get("serial_number"))
print(" Path    :", dinfo.get("path"))
print()

dev = hid.device()
dev.open_path(dinfo["path"])

# Blocking reads with a timeout.
dev.set_nonblocking(False)

print("MasterBus USB Link opened.")
print("Listening for HID input reports for 30 seconds...")
print("NO DATA WILL BE TRANSMITTED.\n")

end_time = time.time() + 30
count = 0

try:
    while time.time() < end_time:
        data = dev.read(256, 1000)

        if data:
            count += 1
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            hexdata = " ".join(f"{b:02X}" for b in data)

            print(
                f"{count:5d}  "
                f"{timestamp}  "
                f"len={len(data):3d}  "
                f"{hexdata}"
            )

finally:
    dev.close()

print()
print(f"Capture finished. Received {count} HID reports.")
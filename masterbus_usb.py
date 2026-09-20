from dataclasses import dataclass
import hid,time
VID,PID=0x1A64,0x0000
REPORT_LEN,RECORD_LEN=64,14

@dataclass(frozen=True)
class CanFrame:
    can_id:int
    data:bytes
    @property
    def can_class(self): return (self.can_id>>24)&0x1F
    @property
    def address(self): return self.can_id&0xFFFFFF

def decode_can_id(r):
    c=r[0]>>3
    hi=((r[0]&7)<<5)|(((r[1]>>5)&7)<<2)|(r[1]&3)
    return (c<<24)|(hi<<16)|(r[2]<<8)|r[3]

def parse_hid_report(report):
    if not report:return []
    out=[]
    for i in range(report[0]):
        b=1+i*RECORD_LEN
        r=report[b:b+RECORD_LEN]
        if len(r)<RECORD_LEN:break
        dlc=min(r[4],8)
        out.append(CanFrame(decode_can_id(r),bytes(r[5:5+dlc])))
    return out

def build_hid_report(can_id,data):
    data=bytes(data)
    c=(can_id>>24)&0x1F; hi=(can_id>>16)&0xFF
    out=bytearray(REPORT_LEN+1); p=memoryview(out)[1:]
    p[0]=1; p[1]=((c<<3)|(hi>>5))&0xFF
    p[2]=((((hi>>2)&7)<<5)|(hi&3))&0xFF
    p[3]=(can_id>>8)&0xFF; p[4]=can_id&0xFF; p[5]=len(data); p[6:6+len(data)]=data
    return bytes(out)

class MasterBusUsb:
    def __init__(self,serial=None): self.serial=serial; self._dev=None; self.device_info=None
    def open(self):
        ds=hid.enumerate(VID,PID)
        if self.serial: ds=[d for d in ds if d.get("serial_number")==self.serial]
        if not ds: raise RuntimeError("MasterBus USB Link not found")
        self.device_info=ds[0]; d=hid.device(); d.open_path(ds[0]["path"]); d.set_nonblocking(False); self._dev=d
    def close(self):
        if self._dev:self._dev.close();self._dev=None
    def read_frames(self,timeout_ms=500):
        if not self._dev: raise RuntimeError("USB Link not open")
        return parse_hid_report(bytes(self._dev.read(REPORT_LEN,timeout_ms)))
    def send_frame(self,can_id,data):
        if self._dev.write(build_hid_report(can_id,data))<=0: raise RuntimeError("USB HID write failed")
    def drain(self,quiet_ms=30,max_ms=250):
        t=time.monotonic(); n=0
        while (time.monotonic()-t)*1000<max_ms:
            f=self.read_frames(quiet_ms)
            if not f:break
            n+=len(f)
        return n

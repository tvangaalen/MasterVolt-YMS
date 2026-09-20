import time

class Discovery:
    def __init__(self,service): self.s=service

    def _request(self,can_id,payload,predicate,timeout=.30,retries=2):
        for _ in range(retries+1):
            with self.s.io_lock:
                self.s.bus.drain()
                self.s.bus.send_frame(can_id,payload)
                end=time.monotonic()+timeout
                while time.monotonic()<end:
                    frames=self.s.bus.read_frames(50)
                    for frame in frames:
                        if predicate(frame): return frame
        return None

    def meta(self,addr,opcode,idx):
        ma=(addr|0x800000)&0xFFFFFF
        payload=bytes([opcode,idx&255,(idx>>8)&255])
        f=self._request((0x18<<24)|ma,payload,lambda x:(
            x.can_class==0x08 and x.address==ma and len(x.data)>=3 and
            x.data[0]==opcode and x.data[1]==(idx&255) and x.data[2]==((idx>>8)&255)
        ))
        return None if f is None else f.data

    def string(self,addr,sid):
        raw=bytearray()
        for seq in range(64):
            payload=bytes([0x30,sid&255,(sid>>8)&255,seq])
            f=self._request((0x07<<24)|addr,payload,lambda x,payload=payload:(
                x.can_class==0x06 and x.address==addr and len(x.data)>=4 and bytes(x.data[:4])==payload
            ))
            if f is None:break
            chunk=bytes(f.data[4:8]); z=chunk.find(b"\0")
            if z>=0:
                raw.extend(chunk[:z]);break
            raw.extend(chunk)
        return raw.decode("latin-1",errors="replace").strip()

    def schema(self,addr,max_index):
        out=[]
        for idx in range(max_index+1):
            nm=self.meta(addr,0x28,idx); un=self.meta(addr,0x2C,idx)
            if nm is None and un is None: continue
            name=unit=""
            if nm and len(nm)>=6:
                sid=int.from_bytes(nm[4:6],"little")
                if sid:name=self.string(addr,sid)
            if un and len(un)>=6:
                sid=int.from_bytes(un[4:6],"little")
                if sid:unit=self.string(addr,sid)
            out.append({"index":idx,"name":name,"unit":unit})
        return out

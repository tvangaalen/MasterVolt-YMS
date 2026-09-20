import math,struct
COMMIT_TOKEN=bytes([0x14,0x9F,0x3C,0x02])

def monitoring_request(addr,field,tab=0):
    return ((0x18<<24)|(addr&0xFFFFFF),bytes([field&0xFF,tab&0xFF]))

def encode_set_float(addr,field,value):
    return ((0x18<<24)|(addr&0xFFFFFF),bytes([field&0xFF,0])+struct.pack("<f",float(value)))

def encode_set_boolean(addr,field,value):
    return encode_set_float(addr,field,1.0 if value else 0.0)

def encode_commit(addr,field):
    return ((0x18<<24)|(addr&0xFFFFFF),bytes([field&0xFF,0])+COMMIT_TOKEN)

def decode_monitoring(frame):
    if frame.can_class!=0x08 or len(frame.data)<6:return None
    v=struct.unpack("<f",bytes(frame.data[2:6]))[0]
    if math.isnan(v) or math.isinf(v):v=None
    return frame.data[0],frame.data[1],v


def btm3_read_request(addr, field):
    alt = (addr | 0x800000) & 0xFFFFFF
    return ((0x1B << 24) | alt, bytes([field & 0xFF, (field >> 8) & 0xFF]))

def btm3_write_float(addr, field, value):
    import struct
    alt = (addr | 0x800000) & 0xFFFFFF
    return (
        (0x1B << 24) | alt,
        bytes([field & 0xFF, (field >> 8) & 0xFF]) + struct.pack("<f", float(value)),
    )

def decode_btm3_value(frame):
    import math, struct
    alt_class = frame.can_class == 0x0B
    if not alt_class or len(frame.data) < 4:
        return None
    value = struct.unpack("<f", bytes(frame.data[-4:]))[0]
    if math.isnan(value) or math.isinf(value):
        value = None
    return value

TS_PACKET_SIZE = 188


def set_discontinuity_segment(segment, force=False):
    """Set the discontinuity flag for the first TS packet in a segment."""
    if len(segment) < TS_PACKET_SIZE:
        raise ValueError("Segment too small for TS packet")
    # Find the first sync byte (0x47)
    for i in range(0, min(1024, len(segment) - TS_PACKET_SIZE + 1), TS_PACKET_SIZE):
        if segment[i] == 0x47:
            first_packet = segment[i:i+TS_PACKET_SIZE]
            new_packet = set_discontinuity_flag(first_packet, force)
            # Replace only the first packet
            return new_packet + segment[i+TS_PACKET_SIZE:]
    raise ValueError("No TS sync byte found in segment")


def shift_segment(segment: bytes, offset: int) -> bytes:
    """
    Shift all TS records in a segment by the given offset (PTS/DTS/PCR).
    Returns the modified segment.
    """
    out = bytearray()
    for i in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_record = segment[i:i+TS_PACKET_SIZE]
        shifted = shift_ts_record(ts_record, offset)
        out.extend(shifted)
    # Append any trailing bytes (if segment is not a multiple of TS_PACKET_SIZE)
    if len(segment) % TS_PACKET_SIZE:
        out.extend(segment[len(out):])
    return bytes(out)


def read_pts_from_segment(segment: bytes, first=True):
    """
    Scan all TS packets in a segment and return the first PTS found (legacy API).
    If first=False, return the last PTS found.
    """
    pts_found = None
    for idx in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_record = segment[idx:idx+TS_PACKET_SIZE]
        pts = read_pts(ts_record)
        if pts is not None:
            if first:
                return pts
            pts_found = pts
    return pts_found


def read_pts_dts(ts_record: bytes):
    if len(ts_record) != TS_PACKET_SIZE:
        return None, None

    if ts_record[0] != 0x47:
        return None, None

    # payload_unit_start = (ts_record[1] & 0x40) >> 6
    adaptation_field_control = (ts_record[3] >> 4) & 0x3

    index = 4
    if adaptation_field_control in (2, 3):
        adaptation_field_length = ts_record[4]
        index += 1 + adaptation_field_length

    # Debug print for diagnosis
    # print(f"TS packet: sync={ts_record[0]:02x}, payload_unit_start={payload_unit_start}, adaptation_field_control={adaptation_field_control}, index={index}")
    # print(f"Bytes at index: {ts_record[index:index+16].hex()}")

    # Scan for PES header start code after adaptation field
    pes_start = ts_record.find(b'\x00\x00\x01', index)
    if pes_start == -1:
        return None, None

    # Check for valid PES header
    if pes_start + 9 + 5 > len(ts_record):
        return None, None

    # stream_id = ts_record[pes_start+3]
    # pes_header_data_length = ts_record[pes_start+8]
    flags = ts_record[pes_start+7]
    pts_dts_flags = (flags >> 6) & 0x3

    pts = dts = None
    if pts_dts_flags & 0x2:
        pts_bytes = ts_record[pes_start+9:pes_start+14]
        if len(pts_bytes) == 5:
            pts = decode_pts_dts(pts_bytes)
    if pts_dts_flags == 0x3:
        dts_bytes = ts_record[pes_start+14:pes_start+19]
        if len(dts_bytes) == 5:
            dts = decode_pts_dts(dts_bytes)

    return pts, dts

def decode_pts_dts(data: bytes):
    pts = (((data[0] >> 1) & 0x07) << 30) | \
          ((data[1] << 22) | (((data[2] >> 1) & 0x7F) << 15) |
          (data[3] << 7) | ((data[4] >> 1) & 0x7F))
    return pts

def encode_pts_dts(value: int, flag_bits: int) -> bytes:
    val = (flag_bits << 4) | (((value >> 30) & 0x07) << 1) | 0x01
    val2 = (((value >> 15) & 0x7FFF) << 1) | 0x01
    val3 = ((value & 0x7FFF) << 1) | 0x01
    return bytes([
        val,
        (val2 >> 8) & 0xFF,
        val2 & 0xFF,
        (val3 >> 8) & 0xFF,
        val3 & 0xFF
    ])

def write_pts(ts_record: bytes, new_pts: int) -> bytes:
    pts, dts = read_pts_dts(ts_record)
    if pts is None:
        return ts_record

    modified = bytearray(ts_record)
    new_pts_bytes = encode_pts_dts(new_pts, 0b10)
    payload_start = modified.find(b'\x00\x00\x01')
    if payload_start == -1:
        return ts_record
    modified[payload_start+9:payload_start+14] = new_pts_bytes
    return bytes(modified)

def read_pts(ts_record: bytes):
    pts, _ = read_pts_dts(ts_record)
    return pts

def read_pcr(ts_record: bytes):
    if len(ts_record) != TS_PACKET_SIZE:
        return None

    adaptation_field_control = (ts_record[3] >> 4) & 0x3
    if adaptation_field_control in (2, 3):
        adaptation_field_length = ts_record[4]
        if adaptation_field_length >= 7 and (ts_record[5] & 0x10):
            pcr_base = (ts_record[6] << 25) | (ts_record[7] << 17) | \
                       (ts_record[8] << 9) | (ts_record[9] << 1) | (ts_record[10] >> 7)
            return pcr_base
    return None

def write_pcr(ts_record: bytes, new_pcr: int) -> bytes:
    if len(ts_record) != TS_PACKET_SIZE:
        return ts_record

    adaptation_field_control = (ts_record[3] >> 4) & 0x3
    if adaptation_field_control in (2, 3):
        adaptation_field_length = ts_record[4]
        if adaptation_field_length >= 7:
            modified = bytearray(ts_record)
            modified[5] |= 0x10  # Ensure PCR flag set
            modified[6] = (new_pcr >> 25) & 0xFF
            modified[7] = (new_pcr >> 17) & 0xFF
            modified[8] = (new_pcr >> 9) & 0xFF
            modified[9] = (new_pcr >> 1) & 0xFF
            modified[10] = ((new_pcr & 0x1) << 7) | 0x7E  # reserved + zeroed extension
            return bytes(modified)
    return ts_record

def shift_pts_dts(ts_record: bytes, offset: int) -> bytes:
    pts, dts = read_pts_dts(ts_record)
    if pts is None:
        return ts_record

    modified = bytearray(ts_record)
    payload_start = modified.find(b'\x00\x00\x01')
    if payload_start == -1:
        return ts_record

    new_pts_bytes = encode_pts_dts(pts + offset, 0b10)
    modified[payload_start+9:payload_start+14] = new_pts_bytes

    if dts is not None:
        new_dts_bytes = encode_pts_dts(dts + offset, 0b01)
        modified[payload_start+14:payload_start+19] = new_dts_bytes

    return bytes(modified)

def shift_pcr(ts_record: bytes, offset: int) -> bytes:
    pcr = read_pcr(ts_record)
    if pcr is None:
        return ts_record
    return write_pcr(ts_record, pcr + offset)

def shift_ts_record(ts_record: bytes, offset: int) -> bytes:
    ts_record = shift_pts_dts(ts_record, offset)
    ts_record = shift_pcr(ts_record, offset)
    return ts_record

def determine_offset(ts_record: bytes) -> int:
    pts = read_pts(ts_record)
    if pts is not None:
        return pts
    pcr = read_pcr(ts_record)
    if pcr is not None:
        return pcr
    raise ValueError("TS record contains neither PTS nor PCR")

def determine_zero_base_offset(ts_record: bytes) -> int:
    offset = determine_offset(ts_record)
    return -offset

def set_discontinuity_flag(ts_record: bytes, force: bool = False) -> bytes:
    if len(ts_record) != TS_PACKET_SIZE:
        raise ValueError("Invalid TS packet size")

    adaptation_field_control = (ts_record[3] >> 4) & 0b11

    if adaptation_field_control in (2, 3):
        adaptation_field_length = ts_record[4]
        if adaptation_field_length == 0 or 5 >= len(ts_record):
            return ts_record

        modified = bytearray(ts_record)
        modified[5] |= 0b10000000  # Set discontinuity_indicator
        return bytes(modified)

    elif force and adaptation_field_control == 1:
        modified = bytearray(ts_record)
        modified[3] = (modified[3] & 0b11001111) | 0b00100000  # Set adaptation field control to 3
        modified.insert(4, 1)       # adaptation_field_length = 1
        modified.insert(5, 0x80)    # flags with discontinuity_indicator

        return bytes(modified[:188])

    return ts_record

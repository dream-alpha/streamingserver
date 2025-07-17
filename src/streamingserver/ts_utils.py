import struct
import subprocess
import json
import tempfile
import os


TS_PACKET_SIZE = 188


def append_iframes_to_ap(segment_data: bytes, ap_file_path: str, segment_offset: int, video_pid: int = 256):
    """
    Scan a TS segment for I-frames and append their byte offsets to a Dreambox-style .ap file.
    Args:
        segment_data: The TS segment data (bytes)
        ap_file_path: Path to the .ap file to append to
        segment_offset: The starting byte offset of this segment in the .ts file
        video_pid: The PID of the video stream (default 256)
    """
    print(f"[AP] append_iframes_to_ap called for {ap_file_path}, segment_offset={segment_offset}, segment_data_len={len(segment_data)}")
    # Auto-detect most frequent video PID in 0x100–0x1FF
    pid_counter = {}
    for i in range(0, len(segment_data) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        pkt = segment_data[i : i + TS_PACKET_SIZE]
        if len(pkt) < TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if 0x100 <= pid <= 0x1FF:
            pid_counter[pid] = pid_counter.get(pid, 0) + 1
    if pid_counter:
        video_pid_auto = max(pid_counter, key=pid_counter.get)
        print(f"[AP] Auto-detected video PID: {video_pid_auto} (counts: {pid_counter})")
    else:
        video_pid_auto = video_pid
        print(f"[AP] No video PID detected in range, using default: {video_pid}")

    offsets = []
    for i in range(0, len(segment_data) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        pkt = segment_data[i : i + TS_PACKET_SIZE]
        if len(pkt) < TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != video_pid_auto:
            continue
        payload_unit_start = (pkt[1] & 0x40) != 0
        if not payload_unit_start:
            continue
        pes_start = pkt.find(b"\x00\x00\x01", 4)
        if pes_start == -1:
            continue
        stream_id = pkt[pes_start + 3] if pes_start + 3 < len(pkt) else None
        if stream_id != 0xE0:
            continue
        # Parse PES header to get payload start
        pes_header_data_length = pkt[pes_start + 8] if pes_start + 8 < len(pkt) else 0
        payload_start = pes_start + 9 + pes_header_data_length
        if payload_start >= len(pkt):
            continue
        payload = pkt[payload_start:]
        # Search for NAL units in payload
        search_offset = 0
        while True:
            nal_idx = payload.find(b"\x00\x00\x01", search_offset)
            if nal_idx == -1 or nal_idx + 4 > len(payload):
                break
            nal_type = payload[nal_idx + 3] & 0x1F
            # print(f"[AP] PID={pid}, stream_id=0x{stream_id:02X} at offset={segment_offset + i}, NAL_type=0x{nal_type:02X}, NAL_offset={nal_idx}")
            if nal_type == 5:
                offsets.append(segment_offset + i)
                break  # Only need first I-frame per packet
            search_offset = nal_idx + 4
    # print(f"[AP] Found {len(offsets)} I-frame offsets: {offsets if offsets else 'None'}")
    try:
        with open(ap_file_path, "ab") as apf:
            if offsets:
                for off in offsets:
                    apf.write(struct.pack(">I", off))
                print(f"[AP] Wrote {len(offsets)} offsets to {ap_file_path}")
            else:
                # Ensure .ap file exists even if no offsets found
                apf.flush()
                print(f"[AP] No I-frames found, .ap file touched: {ap_file_path}")
    except Exception as e:
        print(f"[AP] Error writing to .ap file {ap_file_path}: {e}")
    return offsets


def is_valid_ts_segment(segment_data):
    """Check if segment_data is a valid MPEG-TS segment (sync and video PID)."""
    if not segment_data or len(segment_data) < 188:
        print("[TS_ANALYZE] Segment too short or empty.")
        return False
    sync_count = 0
    pid_counter = {}
    for i in range(0, min(len(segment_data), 188 * 20), 188):
        if i < len(segment_data) and segment_data[i] == 0x47:
            sync_count += 1
        pkt = segment_data[i : i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        pid_counter[pid] = pid_counter.get(pid, 0) + 1
    has_valid_sync = sync_count >= 3
    video_pid_found = any(pid == 256 or (0x100 <= pid <= 0x1FF) for pid in pid_counter)
    print(
        f"[TS_ANALYZE] Segment PIDs: {sorted(pid_counter.keys())}, video_pid_found={video_pid_found}, sync_count={sync_count}"
    )
    return has_valid_sync and video_pid_found


def set_discontinuity_segment(segment, force=False):
    """Set the discontinuity flag for the first TS packet in a segment."""
    if len(segment) < TS_PACKET_SIZE:
        raise ValueError("Segment too small for TS packet")
    # Find the first sync byte (0x47)
    for i in range(0, min(1024, len(segment) - TS_PACKET_SIZE + 1), TS_PACKET_SIZE):
        if segment[i] == 0x47:
            first_packet = segment[i : i + TS_PACKET_SIZE]
            new_packet = set_discontinuity_flag(first_packet, force)
            # Replace only the first packet
            return new_packet + segment[i + TS_PACKET_SIZE :]
    raise ValueError("No TS sync byte found in segment")


def shift_segment(segment: bytes, offset: int) -> bytes:
    """
    Shift all TS records in a segment by the given offset (PTS/DTS/PCR).
    Returns the modified segment.
    """
    out = bytearray()
    for i in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_record = segment[i : i + TS_PACKET_SIZE]
        shifted = shift_ts_record(ts_record, offset)
        out.extend(shifted)
    # Append any trailing bytes (if segment is not a multiple of TS_PACKET_SIZE)
    if len(segment) % TS_PACKET_SIZE:
        out.extend(segment[len(out) :])
    return bytes(out)


def read_pts_from_segment(segment: bytes):
    """
    Scan all TS packets in a segment and return the first and last PTS found.
    Returns tuple (first_pts, last_pts). If only one PTS found, both values are the same.
    If no PTS found, returns (None, None).
    """
    first_pts = None
    last_pts = None
    for idx in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_record = segment[idx : idx + TS_PACKET_SIZE]
        pts = read_pts(ts_record)
        if pts is not None:
            if first_pts is None:
                first_pts = pts
                last_pts = pts
            last_pts = pts
    return first_pts, last_pts


def read_pts_dts(ts_record: bytes):
    if len(ts_record) != TS_PACKET_SIZE:
        return None, None

    if ts_record[0] != 0x47:
        return None, None

    # payload_unit_start = (ts_record[1] & 0x40) >> 6
    adaptation_field_control = (ts_record[3] >> 4) & 0x3

    index = 4
    if adaptation_field_control in {2, 3}:
        adaptation_field_length = ts_record[4]
        index += 1 + adaptation_field_length

    # Debug print for diagnosis
    # print(f"TS packet: sync={ts_record[0]:02x}, payload_unit_start={payload_unit_start}, adaptation_field_control={adaptation_field_control}, index={index}")
    # print(f"Bytes at index: {ts_record[index:index+16].hex()}")

    # Scan for PES header start code after adaptation field
    pes_start = ts_record.find(b"\x00\x00\x01", index)
    if pes_start == -1:
        return None, None

    # Check for valid PES header
    if pes_start + 9 + 5 > len(ts_record):
        return None, None

    # stream_id = ts_record[pes_start+3]
    # pes_header_data_length = ts_record[pes_start+8]
    flags = ts_record[pes_start + 7]
    pts_dts_flags = (flags >> 6) & 0x3

    pts = dts = None
    if pts_dts_flags & 0x2:
        pts_bytes = ts_record[pes_start + 9 : pes_start + 14]
        if len(pts_bytes) == 5:
            pts = decode_pts_dts(pts_bytes)
    if pts_dts_flags == 0x3:
        dts_bytes = ts_record[pes_start + 14 : pes_start + 19]
        if len(dts_bytes) == 5:
            dts = decode_pts_dts(dts_bytes)

    return pts, dts


def decode_pts_dts(data: bytes):
    pts = (((data[0] >> 1) & 0x07) << 30) | (
        (data[1] << 22)
        | (((data[2] >> 1) & 0x7F) << 15)
        | (data[3] << 7)
        | ((data[4] >> 1) & 0x7F)
    )
    return pts


def encode_pts_dts(value: int, flag_bits: int) -> bytes:
    val = (flag_bits << 4) | (((value >> 30) & 0x07) << 1) | 0x01
    val2 = (((value >> 15) & 0x7FFF) << 1) | 0x01
    val3 = ((value & 0x7FFF) << 1) | 0x01
    return bytes(
        [val, (val2 >> 8) & 0xFF, val2 & 0xFF, (val3 >> 8) & 0xFF, val3 & 0xFF]
    )


def write_pts(ts_record: bytes, new_pts: int) -> bytes:
    pts, _dts = read_pts_dts(ts_record)
    if pts is None:
        return ts_record

    modified = bytearray(ts_record)
    new_pts_bytes = encode_pts_dts(new_pts, 0b10)
    payload_start = modified.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_record
    modified[payload_start + 9 : payload_start + 14] = new_pts_bytes
    return bytes(modified)


def read_pts(ts_record: bytes):
    pts, _ = read_pts_dts(ts_record)
    return pts


def read_pcr(ts_record: bytes):
    if len(ts_record) != TS_PACKET_SIZE:
        return None

    adaptation_field_control = (ts_record[3] >> 4) & 0x3
    if adaptation_field_control in {2, 3}:
        adaptation_field_length = ts_record[4]
        if adaptation_field_length >= 7 and (ts_record[5] & 0x10):
            pcr_base = (
                (ts_record[6] << 25)
                | (ts_record[7] << 17)
                | (ts_record[8] << 9)
                | (ts_record[9] << 1)
                | (ts_record[10] >> 7)
            )
            return pcr_base
    return None


def write_pcr(ts_record: bytes, new_pcr: int) -> bytes:
    if len(ts_record) != TS_PACKET_SIZE:
        return ts_record

    adaptation_field_control = (ts_record[3] >> 4) & 0x3
    if adaptation_field_control in {2, 3}:
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
    payload_start = modified.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_record

    new_pts_bytes = encode_pts_dts(pts + offset, 0b10)
    modified[payload_start + 9 : payload_start + 14] = new_pts_bytes

    if dts is not None:
        new_dts_bytes = encode_pts_dts(dts + offset, 0b01)
        modified[payload_start + 14 : payload_start + 19] = new_dts_bytes

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


def set_discontinuity_flag(ts_record: bytes, force: bool = False) -> bytes:
    if len(ts_record) != TS_PACKET_SIZE:
        raise ValueError("Invalid TS packet size")

    adaptation_field_control = (ts_record[3] >> 4) & 0b11

    if adaptation_field_control in {2, 3}:
        adaptation_field_length = ts_record[4]
        if adaptation_field_length == 0 or len(ts_record) <= 5:
            return ts_record

        modified = bytearray(ts_record)
        modified[5] |= 0b10000000  # Set discontinuity_indicator
        return bytes(modified)

    if force and adaptation_field_control == 1:
        modified = bytearray(ts_record)
        modified[3] = (
            modified[3] & 0b11001111
        ) | 0b00100000  # Set adaptation field control to 3
        modified.insert(4, 1)  # adaptation_field_length = 1
        modified.insert(5, 0x80)  # flags with discontinuity_indicator

        return bytes(modified[:188])

    return ts_record


def extract_frame_rate_from_segment_data(segment_data: bytes):
    """
    Extract frame rate from TS segment data using FFmpeg directly.
    Returns the frame rate as a float, or None if not found.
    """
    # Write segment data to a temporary file
    with tempfile.NamedTemporaryFile(suffix='.ts', delete=False) as temp_file:
        temp_file.write(segment_data)
        temp_file_path = temp_file.name

    try:
        # Use ffprobe to get stream information in JSON format
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            temp_file_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)

        if result.returncode != 0:
            print(f"FFprobe failed with error: {result.stderr}")
            return None

        # Parse JSON output
        data = json.loads(result.stdout)

        # Find the first video stream
        video_stream = None
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_stream = stream
                break

        if not video_stream:
            print("No video stream found")
            return None

        # Try to get frame rate from various fields
        frame_rate = None

        # Try r_frame_rate first (most accurate)
        if 'r_frame_rate' in video_stream:
            r_frame_rate = video_stream['r_frame_rate']
            if r_frame_rate and r_frame_rate != '0/0':
                try:
                    num_den = [int(x) for x in r_frame_rate.split('/')]
                    if len(num_den) == 2:
                        num, den = num_den
                        if den != 0:
                            frame_rate = num / den
                except (ValueError, ZeroDivisionError):
                    pass

        # Try avg_frame_rate as fallback
        if frame_rate is None and 'avg_frame_rate' in video_stream:
            avg_frame_rate = video_stream['avg_frame_rate']
            if avg_frame_rate and avg_frame_rate != '0/0':
                try:
                    num_den = [int(x) for x in avg_frame_rate.split('/')]
                    if len(num_den) == 2:
                        num, den = num_den
                        if den != 0:
                            frame_rate = num / den
                except (ValueError, ZeroDivisionError):
                    pass

        # Try time_base as last resort
        if frame_rate is None and 'time_base' in video_stream:
            time_base = video_stream['time_base']
            if time_base and time_base != '0/0':
                try:
                    num_den = [int(x) for x in time_base.split('/')]
                    if len(num_den) == 2:
                        num, den = num_den
                        if den != 0:
                            frame_rate = den / num  # time_base is reciprocal of frame rate
                except (ValueError, ZeroDivisionError):
                    pass

        if frame_rate is None:
            print("Frame rate not available in stream metadata")
            return None

        return frame_rate

    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"Error running FFprobe: {e}")
        return None
    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass

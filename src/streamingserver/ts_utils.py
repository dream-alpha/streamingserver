from typing import Tuple
import os
import subprocess
import json
import tempfile


TS_PACKET_SIZE = 188


def find_gop_start_in_segment(segment_bytes: bytes):
    """
    Find the first clean GOP (SPS+PPS+IDR) in an HLS .ts segment.

    Args:
        segment_bytes (bytes): Entire .ts segment as a bytes object.

    Returns:
        (int, int, bytes, bytes) or (None, None, None, None):
        Tuple of (GOP byte offset, video PID, PAT packet, PMT packet),
        or all None if no clean GOP is found.
    """

    def read_ts_packets(data):
        for i in range(0, len(data), TS_PACKET_SIZE):
            packet = data[i:i + TS_PACKET_SIZE]
            if len(packet) == TS_PACKET_SIZE:
                yield packet, i

    def parse_pid(packet):
        return ((packet[1] & 0x1F) << 8) | packet[2]

    def payload_unit_start(packet):
        return (packet[1] & 0x40) != 0

    def get_payload(packet):
        adaptation = (packet[3] >> 4) & 0x3
        offset = 4
        if adaptation in (2, 3):  # has adaptation field
            offset += 1 + packet[4]
        return packet[offset:]

    pat_pid = 0x0000
    pmt_pid = None
    video_pid = None

    sps_seen = False
    pps_seen = False
    pes_buffer = bytearray()

    last_pat_pkt = None
    last_pmt_pkt = None

    for pkt, pkt_offset in read_ts_packets(segment_bytes):
        if pkt[0] != 0x47:
            continue

        pid = parse_pid(pkt)
        payload = get_payload(pkt)

        # --- Parse PAT ---
        if pid == pat_pid and payload_unit_start(pkt):
            last_pat_pkt = pkt
            section_start = 1 + payload[0]
            section = payload[section_start:]
            if len(section) >= 12:
                pmt_pid = ((section[10] & 0x1F) << 8) | section[11]

        # --- Parse PMT ---
        elif pmt_pid and pid == pmt_pid and payload_unit_start(pkt):
            last_pmt_pkt = pkt
            section_start = 1 + payload[0]
            section = payload[section_start:]
            if len(section) >= 13:
                program_info_len = ((section[10] & 0x0F) << 8) | section[11]
                idx = 12 + program_info_len
                while idx < len(section) - 4:
                    stream_type = section[idx]
                    elementary_pid = ((section[idx+1] & 0x1F) << 8) | section[idx+2]
                    es_info_len = ((section[idx+3] & 0x0F) << 8) | section[idx+4]
                    if stream_type in (0x1B, 0x24):  # H.264 or H.265
                        video_pid = elementary_pid
                        break
                    idx += 5 + es_info_len

        # --- Scan for GOP start ---
        elif video_pid and pid == video_pid:
            pes_buffer.extend(payload)

            i = 0
            while i < len(pes_buffer) - 4:
                if pes_buffer[i:i+3] == b'\x00\x00\x01':
                    nal_type = pes_buffer[i+3] & 0x1F
                    if nal_type == 7:
                        sps_seen = True
                    elif nal_type == 8:
                        pps_seen = True
                    elif nal_type == 5:
                        if sps_seen and pps_seen:
                            return pkt_offset, video_pid, last_pat_pkt, last_pmt_pkt
                i += 1

    return -1, None, None, None


def update_continuity_counters(segment: bytes, starting_cc: int) -> Tuple[int, bytes]:
    """
    Incrementally update the continuity counter (CC) for all TS packets in a segment.
    Input: starting_cc (int, 0-15), segment (bytes)
    Returns: (last_cc, modified_segment)
    """
    if len(segment) % TS_PACKET_SIZE != 0:
        raise ValueError("Segment size is not a multiple of TS_PACKET_SIZE")
    out = bytearray()
    cc = starting_cc & 0x0F
    for i in range(0, len(segment), TS_PACKET_SIZE):
        pkt = bytearray(segment[i:i+TS_PACKET_SIZE])
        if len(pkt) != TS_PACKET_SIZE or pkt[0] != 0x47:
            out.extend(pkt)
            continue
        pkt[3] = (pkt[3] & 0xF0) | cc
        out.extend(pkt)
        cc = (cc + 1) & 0x0F
    last_cc = (cc - 1) & 0x0F if len(segment) > 0 else starting_cc
    return last_cc, bytes(out)


def read_continuity_counter(ts_packet: bytes) -> int:
    """
    Extract the continuity counter (CC) from a 188-byte MPEG-TS packet.
    Returns the 4-bit CC value as an integer (0-15).
    Raises ValueError if the packet is not 188 bytes or does not start with 0x47.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        raise ValueError("Invalid TS packet size")
    if ts_packet[0] != 0x47:
        raise ValueError("Invalid TS sync byte")
    return ts_packet[3] & 0x0F


def make_discontinuity_packet(pid: int = 0x1FFF, continuity_counter: int = 0) -> bytes:
    """
    Create a single MPEG-TS packet (188 bytes) with only the discontinuity flag set in the adaptation field.
    By default, uses null packet PID (0x1FFF) and continuity_counter=0.
    """
    packet = bytearray(188)
    packet[0] = 0x47  # Sync byte
    # Set PID
    packet[1] = 0x40 | ((pid >> 8) & 0x1F)  # payload_unit_start_indicator=0, PID high bits
    packet[2] = pid & 0xFF  # PID low bits
    # Adaptation field only, no payload
    packet[3] = 0x20 | (continuity_counter & 0x0F)  # adaptation_field_control=2
    packet[4] = 1  # adaptation_field_length=1 (only flags byte)
    packet[5] = 0x80  # discontinuity_indicator=1
    # Stuffing bytes (0xFF) for rest of adaptation field (none needed here, since length=1)
    for i in range(6, 188):
        packet[i] = 0xFF
    return bytes(packet)


def segment_has_pat_pmt(segment_data: bytes) -> int:
    """
    Check if the TS segment contains a valid PAT and PMT.
    Returns the index of the TS packet containing the PMT, or -1 if not found.
    """
    # PAT PID is always 0x0000, PMT PID is found in PAT
    pat_idx = -1
    pmt_pid = None
    pmt_idx = -1
    num_packets = len(segment_data) // TS_PACKET_SIZE
    for i in range(num_packets):
        pkt = segment_data[i * TS_PACKET_SIZE: (i + 1) * TS_PACKET_SIZE]
        if len(pkt) < TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        # Check for PAT (PID 0)
        if pid == 0x0000 and pat_idx == -1:
            # Parse PAT to find PMT PID
            pointer_field = pkt[4]
            pat_start = 5 + pointer_field
            # Check table_id
            if pat_start + 8 < len(pkt) and pkt[pat_start] == 0x00:
                section_length = ((pkt[pat_start + 1] & 0x0F) << 8) | pkt[pat_start + 2]
                # Loop through PAT program info
                # Each program info is 4 bytes, starts at pat_start+8
                program_info_start = pat_start + 8
                program_info_end = pat_start + 3 + section_length - 4  # -4 for CRC
                for j in range(program_info_start, program_info_end, 4):
                    if j + 4 > len(pkt):
                        break
                    program_number = (pkt[j] << 8) | pkt[j + 1]
                    if program_number == 0:
                        continue  # network PID, skip
                    pmt_pid_candidate = ((pkt[j + 2] & 0x1F) << 8) | pkt[j + 3]
                    pmt_pid = pmt_pid_candidate
                    pat_idx = i
                    break
        # Check for PMT
        if pmt_pid is not None and pid == pmt_pid and pmt_idx == -1:
            # PMT table_id is 0x02
            pointer_field = pkt[4]
            pmt_start = 5 + pointer_field
            if pmt_start < len(pkt) and pkt[pmt_start] == 0x02:
                pmt_idx = i
                break  # Found PMT, can stop
    return pat_idx, pmt_idx


def segment_has_keyframe(segment_data: bytes) -> int:
    """
    Check if the TS segment contains a video keyframe (IDR) at or near the start.
    Returns the packet index (0-based) if a keyframe is found in the first N packets, else -1.
    """
    # MPEG-TS: Look for NAL unit type 5 (IDR) in H.264 or type 19/20 in H.265
    # Only check first 20 packets for speed
    max_packets = min(len(segment_data) // TS_PACKET_SIZE, 20)
    for i in range(0, max_packets * TS_PACKET_SIZE, TS_PACKET_SIZE):
        pkt = segment_data[i:i + TS_PACKET_SIZE]
        if len(pkt) < TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        # Check for PES start
        payload_unit_start = (pkt[1] & 0x40) >> 6
        adaptation_field_control = (pkt[3] >> 4) & 0x3
        index = 4
        if adaptation_field_control in {2, 3}:
            adaptation_field_length = pkt[4]
            index += 1 + adaptation_field_length
        if payload_unit_start:
            pes_start = pkt.find(b"\x00\x00\x01", index)
            if pes_start == -1:
                continue
            # Check for H.264/AVC or H.265/HEVC NAL units
            # Find NAL unit start code (0x000001) after PES header
            # PES header is at least 9 bytes after start code
            pes_header_data_length = pkt[pes_start + 8] if pes_start + 8 < len(pkt) else 0
            es_data_start = pes_start + 9 + pes_header_data_length
            es_data = pkt[es_data_start:]
            # Search for NAL start code (0x000001)
            for j in range(len(es_data) - 4):
                if es_data[j:j + 3] == b"\x00\x00\x01":
                    nal_unit_type = es_data[j + 3] & 0x1F  # H.264
                    if nal_unit_type == 5:
                        return i // TS_PACKET_SIZE  # IDR frame (keyframe), return packet index
                    # H.265/HEVC: nal_unit_type is 6 bits (bits 1-6 of byte after start code)
                    hevc_type = (es_data[j + 3] >> 1) & 0x3F
                    if hevc_type in {19, 20}:
                        return i // TS_PACKET_SIZE  # IDR_W_RADL or IDR_N_LP, return packet index
    return -1


def is_valid_ts_segment(segment_data):
    """Check if segment_data is a valid MPEG-TS segment (sync and video PID)."""
    if not segment_data or len(segment_data) < 188:
        print("[TS_ANALYZE] Segment too short or empty.")
        return False
    sync_count = 0
    pid_counter = {}
    corrupted_packets = []
    total_packets = min(len(segment_data) // 188, 20)
    for i in range(0, total_packets * 188, 188):
        pkt = segment_data[i: i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            corrupted_packets.append((i, pkt))
            continue
        sync_count += 1
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        pid_counter[pid] = pid_counter.get(pid, 0) + 1

    # Stricter checks
    min_sync_packets = max(3, int(0.8 * total_packets))
    has_valid_sync = sync_count >= min_sync_packets
    if not has_valid_sync:
        print(f"[TS_ANALYZE] Sync byte check failed: {sync_count}/{total_packets} valid packets (need >= {min_sync_packets})")

    # Find majority video PID
    video_pids = [pid for pid in pid_counter if pid == 256 or (0x100 <= pid <= 0x1FF)]
    video_pid_count = sum(pid_counter[pid] for pid in video_pids)
    majority_video_pid = video_pid_count >= int(0.5 * sync_count) and video_pid_count > 0
    if not majority_video_pid:
        print(f"[TS_ANALYZE] Video PID check failed: {video_pid_count}/{sync_count} packets with video PID (need >= {int(0.5 * sync_count)})")

    # Check for valid PTS/DTS in video packets
    pts_dts_valid_count = 0
    pts_dts_invalid_count = 0
    for i in range(0, total_packets * 188, 188):
        pkt = segment_data[i: i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == 256 or (0x100 <= pid <= 0x1FF):
            pts, dts = read_pts_dts(pkt)
            if pts is not None and (dts is not None or dts is None):
                pts_dts_valid_count += 1
            else:
                pts_dts_invalid_count += 1

    # MPEG-TS spec: only require at least one valid PTS/DTS in video packets
    has_valid_pts_dts = pts_dts_valid_count >= 1
    if not has_valid_pts_dts:
        print(f"[TS_ANALYZE] PTS/DTS check failed: {pts_dts_valid_count}/{video_pid_count} video packets with valid PTS/DTS (need >= 1)")

    print(f"[TS_ANALYZE] Segment PIDs: {sorted(pid_counter.keys())}, video_pid_count={video_pid_count}, sync_count={sync_count}, pts_dts_valid_count={pts_dts_valid_count}")
    if corrupted_packets:
        print("[TS_ANALYZE] Corrupted TS packets in segment:")
        for idx, pkt in corrupted_packets:
            print(f"  Offset {idx}: {pkt.hex()}")

    return has_valid_sync and majority_video_pid and has_valid_pts_dts


def set_discontinuity_segment(segment, force=False):
    """Set the discontinuity flag for all TS packets in a segment."""
    if len(segment) < TS_PACKET_SIZE:
        raise ValueError("Segment too small for TS packet")
    out = bytearray()
    seen_pids = set()
    for i in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        pkt = segment[i: i + TS_PACKET_SIZE]
        if len(pkt) == TS_PACKET_SIZE and pkt[0] == 0x47:
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            if pid not in seen_pids:
                pkt = set_discontinuity_flag(pkt, force)
                seen_pids.add(pid)
        out.extend(pkt)
    # Append any trailing bytes (if segment is not a multiple of TS_PACKET_SIZE)
    if len(segment) % TS_PACKET_SIZE:
        out.extend(segment[len(out):])
    return bytes(out)


def shift_segment(segment: bytes, offset: int) -> bytes:
    """
    Shift all TS records in a segment by the given offset (PTS/DTS/PCR).
    Returns the modified segment.
    """
    out = bytearray()
    for i in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_record = segment[i: i + TS_PACKET_SIZE]
        shifted = shift_ts_record(ts_record, offset)
        out.extend(shifted)
    # Append any trailing bytes (if segment is not a multiple of TS_PACKET_SIZE)
    if len(segment) % TS_PACKET_SIZE:
        out.extend(segment[len(out):])
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
        ts_record = segment[idx: idx + TS_PACKET_SIZE]
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
        pts_bytes = ts_record[pes_start + 9: pes_start + 14]
        if len(pts_bytes) == 5:
            pts = decode_pts_dts(pts_bytes)
    if pts_dts_flags == 0x3:
        dts_bytes = ts_record[pes_start + 14: pes_start + 19]
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
    modified[payload_start + 9: payload_start + 14] = new_pts_bytes
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
    modified[payload_start + 9: payload_start + 14] = new_pts_bytes

    if dts is not None:
        new_dts_bytes = encode_pts_dts(dts + offset, 0b01)
        modified[payload_start + 14: payload_start + 19] = new_dts_bytes

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

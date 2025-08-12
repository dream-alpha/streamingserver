"""
MPEG-TS (Transport Stream) Utilities

This module provides a comprehensive set of low-level utilities for parsing,
analyzing, and manipulating MPEG-TS packets and segments. It includes functions
for reading and writing timestamps (PTS, DTS, PCR), finding and parsing PSI
tables (PAT, PMT), identifying keyframes (IDRs), managing continuity counters,
and validating the integrity of TS segments. These tools are fundamental for
processing HLS streams, stitching segments, and ensuring stream compliance.
"""
import os
import subprocess
import json
import tempfile
from debug import get_logger

logger = get_logger(__file__)

TS_PACKET_SIZE = 188


def get_pcr_diff(segment_data: bytes):
    """
    Calculates the difference between the first two PCRs in a segment.

    This is useful for estimating the segment's duration or clock rate.

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple containing (first_pcr_base, difference). Returns (None, None)
        if fewer than two PCRs are found.
    """
    first_pcr = None
    second_pcr = None
    for i in range(0, len(segment_data) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        pkt = segment_data[i:i + TS_PACKET_SIZE]
        pcr = read_pcr(pkt)
        if pcr is not None:
            if isinstance(pcr, tuple):
                base, _ = pcr
            else:
                base = pcr
            if first_pcr is None:
                first_pcr = base
            elif second_pcr is None:
                second_pcr = base
                break
    if first_pcr is not None and second_pcr is not None:
        return first_pcr, second_pcr - first_pcr
    return None, None


def find_video_pid_in_segment(segment_bytes: bytes) -> int:
    """
    Finds the video PID in a TS segment by parsing PAT and PMT tables.

    It first finds the Program Association Table (PAT) to get the PID of the
    Program Map Table (PMT). Then, it parses the PMT to find the PID of the

    first video stream (H.264/H.265).

    Args:
        segment_bytes: The raw bytes of the MPEG-TS segment.

    Returns:
        The PID of the first video stream found, or None if not found.
    """
    def read_ts_packets(data):
        for i in range(0, len(data), TS_PACKET_SIZE):
            packet = data[i:i + TS_PACKET_SIZE]
            if len(packet) == TS_PACKET_SIZE:
                yield packet, i

    pat_pid = 0x0000
    pmt_pid = None
    # --- Find PMT PID from PAT ---
    for pkt, _ in read_ts_packets(segment_bytes):
        if pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == pat_pid:
            pointer_field = pkt[4]
            pat_start = 5 + pointer_field
            if pat_start + 8 < len(pkt) and pkt[pat_start] == 0x00:
                section_length = ((pkt[pat_start + 1] & 0x0F) << 8) | pkt[pat_start + 2]
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
                    break
        if pmt_pid is not None:
            break

    if pmt_pid is None:
        return None

    # --- Find video PID from PMT ---
    for pkt, _ in read_ts_packets(segment_bytes):
        if pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == pmt_pid:
            pointer_field = pkt[4]
            pmt_start = 5 + pointer_field
            if pmt_start < len(pkt) and pkt[pmt_start] == 0x02:
                section_length = ((pkt[pmt_start + 1] & 0x0F) << 8) | pkt[pmt_start + 2]
                program_info_length = ((pkt[pmt_start + 10] & 0x0F) << 8) | pkt[pmt_start + 11]
                idx = pmt_start + 12 + program_info_length
                section_end = pmt_start + 3 + section_length - 4  # -4 for CRC
                while idx + 5 <= min(section_end, len(pkt)):
                    stream_type = pkt[idx]
                    elementary_pid = ((pkt[idx + 1] & 0x1F) << 8) | pkt[idx + 2]
                    es_info_length = ((pkt[idx + 3] & 0x0F) << 8) | pkt[idx + 4]
                    if stream_type in {0x1B, 0x24}:  # H.264 or H.265
                        return elementary_pid
                    idx += 5 + es_info_length
    return None


def find_pat_pmt_in_segment(segment_bytes: bytes):
    """
    Finds and returns the first PAT and PMT packets in a segment.

    Args:
        segment_bytes: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple containing (pat_packet, pmt_packet, pat_packet_index).
        Returns (None, None, -1) if not found.
    """
    pat_pid = 0x0000
    pmt_pid = None
    pat_packet = None
    pmt_packet = None
    pat_index = -1
    # First, find PAT and extract PMT PID
    for i in range(0, len(segment_bytes), TS_PACKET_SIZE):
        pkt = segment_bytes[i:i + TS_PACKET_SIZE]
        if len(pkt) != TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == pat_pid:
            pointer_field = pkt[4]
            pat_start = 5 + pointer_field
            if pat_start + 8 < len(pkt) and pkt[pat_start] == 0x00:
                section_length = ((pkt[pat_start + 1] & 0x0F) << 8) | pkt[pat_start + 2]
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
                    pat_packet = pkt
                    pat_index = i // TS_PACKET_SIZE
                    break
        if pat_packet is not None and pmt_pid is not None:
            break

    if pmt_pid is None or pat_packet is None:
        return None, None, -1

    # Now, find PMT packet
    for i in range(0, len(segment_bytes), TS_PACKET_SIZE):
        pkt = segment_bytes[i:i + TS_PACKET_SIZE]
        if len(pkt) != TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == pmt_pid:
            pointer_field = pkt[4]
            pmt_start = 5 + pointer_field
            if pmt_start < len(pkt) and pkt[pmt_start] == 0x02:
                pmt_packet = pkt
                break

    return pat_packet, pmt_packet, pat_index


def find_gop_start_in_segment(segment_bytes: bytes):
    """
    Finds the first clean Group of Pictures (GOP) start in a segment.

    A clean GOP start is defined as a sequence of SPS, PPS, and IDR NAL units
    in the video stream, which allows a decoder to start playback without
    prior data.

    Args:
        segment_bytes: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple of (gop_packet_index, video_pid, pat_packet, pmt_packet),
        or (-1, None, None, None) if no clean GOP is found.
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
        if adaptation in {2, 3}:  # has adaptation field
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
                    elementary_pid = ((section[idx + 1] & 0x1F) << 8) | section[idx + 2]
                    es_info_len = ((section[idx + 3] & 0x0F) << 8) | section[idx + 4]
                    if stream_type in {0x1B, 0x24}:  # H.264 or H.265
                        video_pid = elementary_pid
                        break
                    idx += 5 + es_info_len

        # --- Scan for GOP start ---
        elif video_pid and pid == video_pid:
            pes_buffer.extend(payload)

            i = 0
            while i < len(pes_buffer) - 4:
                if pes_buffer[i:i + 3] == b'\x00\x00\x01':
                    nal_type = pes_buffer[i + 3] & 0x1F
                    if nal_type == 7:
                        sps_seen = True
                    elif nal_type == 8:
                        pps_seen = True
                    elif nal_type == 5:
                        if sps_seen and pps_seen:
                            return pkt_offset // TS_PACKET_SIZE, video_pid, last_pat_pkt, last_pmt_pkt
                i += 1

    return -1, None, None, None


def update_continuity_counters(segment: bytes, cc_map: dict) -> tuple[bytes, dict]:
    """
    Incrementally updates the continuity counter (CC) for each PID in a segment.

    This function ensures that the CC for each PID is continuous, which is
    required for a compliant MPEG-TS stream.

    Args:
        segment: The raw bytes of the MPEG-TS segment.
        cc_map: A dictionary mapping PIDs to their last known CC value.

    Returns:
        A tuple containing the modified segment bytes and the updated cc_map.
    """
    out = bytearray()
    cc_map = cc_map.copy() if cc_map else {}
    for i in range(0, len(segment), TS_PACKET_SIZE):
        pkt = bytearray(segment[i:i + TS_PACKET_SIZE])
        if len(pkt) != TS_PACKET_SIZE or pkt[0] != 0x47:
            out.extend(pkt)
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        cc = (cc_map.get(pid, -1) + 1) & 0x0F
        pkt[3] = (pkt[3] & 0xF0) | cc
        out.extend(pkt)
        cc_map[pid] = cc
    return bytes(out), cc_map


def read_continuity_counter(ts_packet: bytes) -> int:
    """
    Extracts the continuity counter (CC) from a single TS packet.

    Args:
        ts_packet: A 188-byte MPEG-TS packet.

    Returns:
        The 4-bit CC value as an integer (0-15).

    Raises:
        ValueError: If the packet is not a valid TS packet.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        raise ValueError("Invalid TS packet size")
    if ts_packet[0] != 0x47:
        raise ValueError("Invalid TS sync byte")
    return ts_packet[3] & 0x0F


def segment_has_pat_pmt(segment_data: bytes) -> int:
    """
    Checks if a TS segment contains a valid PAT and PMT.

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple of (pat_packet_index, pmt_packet_index), or (-1, -1) if not found.
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
    Checks if a TS segment contains a video keyframe (IDR) near the start.

    It scans the first few packets of the segment for a PES packet containing
    an H.264 IDR frame (NAL unit type 5) or an H.265 IDR frame (NAL unit
    type 19 or 20).

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        The 0-based index of the packet containing the keyframe, or -1 if not found.
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


def is_valid_ts_segment(segment_data: bytes):
    """
    Performs a series of checks to validate if a segment is a valid MPEG-TS segment.

    Checks for:
    - A minimum number of TS sync bytes.
    - A majority of packets belonging to a plausible video PID.
    - The presence of at least one valid PTS/DTS timestamp in video packets.

    Args:
        segment_data (bytes): The raw bytes of the MPEG-TS segment.

    Returns:
        True if the segment passes validation checks, False otherwise.
    """
    if not segment_data or len(segment_data) < 188:
        logger.debug("[TS_ANALYZE] Segment too short or empty: %s", len(segment_data))
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
        logger.debug("[TS_ANALYZE] Sync byte check failed: %s/%s valid packets (need >= %s)", sync_count, total_packets, min_sync_packets)

    # Find majority video PID
    video_pids = [pid for pid in pid_counter if pid == 256 or (0x100 <= pid <= 0x1FF)]
    video_pid_count = sum(pid_counter[pid] for pid in video_pids)
    majority_video_pid = video_pid_count >= int(0.5 * sync_count) and video_pid_count > 0
    if not majority_video_pid:
        logger.debug("[TS_ANALYZE] Video PID check failed: %s/%s packets with video PID (need >= %s)", video_pid_count, sync_count, int(0.5 * sync_count))

    # Check for valid PTS/DTS in video packets
    pts_dts_valid_count = 0
    pts_dts_invalid_count = 0
    for i in range(0, total_packets * 188, 188):
        pkt = segment_data[i: i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid == 256 or (0x100 <= pid <= 0x1FF):
            pts = read_pts(pkt)
            dts = read_dts(pkt)
            if pts is not None and (dts is not None or dts is None):
                pts_dts_valid_count += 1
            else:
                pts_dts_invalid_count += 1

    # MPEG-TS spec: only require at least one valid PTS/DTS in video packets
    has_valid_pts_dts = pts_dts_valid_count >= 1
    if not has_valid_pts_dts:
        logger.debug("[TS_ANALYZE] PTS/DTS check failed: %s/%s video packets with valid PTS/DTS (need >= 1)", pts_dts_valid_count, video_pid_count)

    logger.debug("[TS_ANALYZE] Segment PIDs: %s, video_pid_count=%s, sync_count=%s, pts_dts_valid_count=%s", sorted(pid_counter.keys()), video_pid_count, sync_count, pts_dts_valid_count)
    if corrupted_packets:
        logger.debug("[TS_ANALYZE] Corrupted TS packets in segment:")
        for idx, pkt in corrupted_packets:
            logger.debug("  Offset %s: %s", idx, pkt.hex())

    return has_valid_sync and majority_video_pid and has_valid_pts_dts


def set_discontinuity_segment(segment: bytes) -> bytes:
    """
    Sets the discontinuity indicator on the first possible packet in a segment.

    According to the HLS specification, the discontinuity indicator flag should be
    set on the first packet of each elementary stream after a discontinuity. A
    simpler, common practice is to set it on the first packet of the segment
    that has an adaptation field.

    Args:
        segment: The raw bytes of the MPEG-TS segment.

    Returns:
        The modified segment with the discontinuity flag set, or the original
        segment if no suitable packet is found.
    """
    if not segment:
        return segment

    modified_segment = bytearray(segment)

    # Iterate through packets to find the first one with an adaptation field
    for i in range(0, len(modified_segment), TS_PACKET_SIZE):
        if i + TS_PACKET_SIZE > len(modified_segment):
            break  # Avoid partial packet

        packet_offset = i
        adaptation_field_control = (modified_segment[packet_offset + 3] >> 4) & 0b11

        # Check if an adaptation field exists (0b10 or 0b11)
        if adaptation_field_control in {2, 3}:
            adaptation_field_length = modified_segment[packet_offset + 4]
            # Ensure there's room for the flag byte
            if adaptation_field_length > 0:
                # Set the discontinuity_indicator bit (bit 7 of the flags byte)
                modified_segment[packet_offset + 5] |= 0b10000000
                logger.debug("Discontinuity flag set on packet at offset %s", packet_offset)
                return bytes(modified_segment)

    # If no packet with an adaptation field was found, we can't set the flag.
    # This is rare but we return the original segment to avoid errors.
    logger.warning("Could not find a suitable packet to set the discontinuity flag.")
    return segment


def shift_segment(segment: bytes, offset: int) -> bytes:
    """
    Shifts all timestamps (PTS, DTS, PCR) in a segment by a given offset.

    Args:
        segment: The raw bytes of the MPEG-TS segment.
        offset: The value to add to each timestamp.

    Returns:
        The modified segment with shifted timestamps.
    """
    out = bytearray()
    for i in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_packet = segment[i: i + TS_PACKET_SIZE]
        shifted = shift_ts_packet(ts_packet, offset)
        out.extend(shifted)
    # Append any trailing bytes (if segment is not a multiple of TS_PACKET_SIZE)
    if len(segment) % TS_PACKET_SIZE:
        out.extend(segment[len(out):])
    return bytes(out)


def read_pts_from_segment(segment: bytes):
    """
    Scans all TS packets in a segment and returns the first and last PTS found.

    Args:
        segment: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple (first_pts, last_pts). If only one PTS is found, both values
        are the same. If no PTS is found, returns (None, None).
    """
    first_pts = None
    last_pts = None
    for idx in range(0, len(segment) - TS_PACKET_SIZE + 1, TS_PACKET_SIZE):
        ts_packet = segment[idx: idx + TS_PACKET_SIZE]
        pts = read_pts(ts_packet)
        if pts is not None:
            if first_pts is None:
                first_pts = pts
                last_pts = pts
            last_pts = pts
    return first_pts, last_pts


def read_pts(ts_packet: bytes):
    """
    Extracts the Presentation Timestamp (PTS) from a single TS packet.

    Args:
        ts_packet: A 188-byte MPEG-TS packet.

    Returns:
        The PTS value as an integer, or None if not present.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        return None
    if ts_packet[0] != 0x47:
        return None
    adaptation_field_control = (ts_packet[3] >> 4) & 0x3
    index = 4
    if adaptation_field_control in {2, 3}:
        adaptation_field_length = ts_packet[4]
        index += 1 + adaptation_field_length
    pes_start = ts_packet.find(b"\x00\x00\x01", index)
    if pes_start == -1:
        return None
    # Check PES header bounds
    if pes_start + 14 > len(ts_packet):
        return None
    pes_header_len = ts_packet[pes_start + 8]
    flags = ts_packet[pes_start + 7]
    pts_dts_flags = (flags >> 6) & 0x3
    # Only read PTS if PTS-only (0b10) or PTS+DTS (0b11) is set and header length is sufficient
    if pts_dts_flags == 0x2 and pes_header_len >= 5:
        pts_bytes = ts_packet[pes_start + 9: pes_start + 14]
        if len(pts_bytes) == 5:
            return decode_pts_dts(pts_bytes)
    elif pts_dts_flags == 0x3 and pes_header_len >= 10:
        pts_bytes = ts_packet[pes_start + 9: pes_start + 14]
        if len(pts_bytes) == 5:
            return decode_pts_dts(pts_bytes)
    return None


def read_dts(ts_packet: bytes):
    """
    Extracts the Decoding Timestamp (DTS) from a single TS packet.

    Args:
        ts_packet: A 188-byte MPEG-TS packet.

    Returns:
        The DTS value as an integer, or None if not present.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        return None
    if ts_packet[0] != 0x47:
        return None
    adaptation_field_control = (ts_packet[3] >> 4) & 0x3
    index = 4
    if adaptation_field_control in {2, 3}:
        adaptation_field_length = ts_packet[4]
        index += 1 + adaptation_field_length
    pes_start = ts_packet.find(b"\x00\x00\x01", index)
    if pes_start == -1:
        return None
    if pes_start + 14 + 5 > len(ts_packet):
        return None
    flags = ts_packet[pes_start + 7]
    pts_dts_flags = (flags >> 6) & 0x3
    if pts_dts_flags == 0x3:
        dts_bytes = ts_packet[pes_start + 14: pes_start + 19]
        if len(dts_bytes) == 5:
            return decode_pts_dts(dts_bytes)
    return None


def decode_pts_dts(data: bytes):
    """
    Decodes a 5-byte PTS/DTS field into a 33-bit integer value.

    Args:
        data: A 5-byte array containing the encoded timestamp.

    Returns:
        The decoded 33-bit timestamp value.
    """
    pts = (((data[0] >> 1) & 0x07) << 30) | (
        (data[1] << 22)
        | (((data[2] >> 1) & 0x7F) << 15)
        | (data[3] << 7)
        | ((data[4] >> 1) & 0x7F)
    )
    return pts


def encode_pts_dts(value: int, flag_bits: int) -> bytes:
    """
    Encodes a 33-bit PTS/DTS value into a 5-byte field.

    Args:
        value: The 33-bit integer timestamp.
        flag_bits: The 2-bit flag to set (e.g., 0b10 for PTS, 0b01 for DTS).

    Returns:
        A 5-byte array with the encoded timestamp.
    """
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


# helper: encode_pts_dts_preserve
def encode_pts_dts_preserve(value: int, orig_bytes: bytes) -> bytes:
    """
    Encodes a PTS/DTS value while preserving marker/reserved bits from original.

    Args:
        value: The new 33-bit integer timestamp.
        orig_bytes: The original 5-byte encoded timestamp field.

    Returns:
        A new 5-byte array with the updated timestamp.
    """
    if len(orig_bytes) != 5:
        return encode_pts_dts(value, 0b10)
    # Extract bits from orig_bytes
    # Byte 0: 4 bits flags, 3 bits timestamp, 1 marker
    # Byte 1: 8 bits timestamp
    # Byte 2: 7 bits timestamp, 1 marker
    # Byte 3: 8 bits timestamp
    # Byte 4: 7 bits timestamp, 1 marker
    b0 = (orig_bytes[0] & 0xF0) | (((value >> 30) & 0x07) << 1) | (orig_bytes[0] & 0x01)
    b1 = (value >> 22) & 0xFF
    b2 = ((orig_bytes[2] & 0x01) | (((value >> 15) & 0x7F) << 1))
    b3 = (value >> 7) & 0xFF
    b4 = ((orig_bytes[4] & 0x01) | (((value & 0x7F) << 1)))
    return bytes([b0, b1, b2, b3, b4])


def write_pts(ts_packet: bytes, new_pts: int) -> bytes:
    """
    Overwrites the PTS field in a TS packet's PES header.

    Args:
        ts_packet: The original TS packet.
        new_pts: The new PTS value to write.

    Returns:
        The modified TS packet.
    """
    pts = read_pts(ts_packet)
    if pts is None:
        return ts_packet

    payload_start = ts_packet.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_packet

    # Get original PTS bytes
    orig_pts_bytes = ts_packet[payload_start + 9: payload_start + 14]
    new_pts_bytes = encode_pts_dts_preserve(new_pts, orig_pts_bytes)

    if orig_pts_bytes == new_pts_bytes:
        # No change needed, and encoding matches
        return ts_packet
    # Write new PTS, preserving marker/reserved bits
    modified = bytearray(ts_packet)
    modified[payload_start + 9: payload_start + 14] = new_pts_bytes
    return bytes(modified)


def write_dts(ts_packet: bytes, new_dts: int) -> bytes:
    """
    Overwrites the DTS field in a TS packet's PES header.

    Args:
        ts_packet: The original TS packet.
        new_dts: The new DTS value to write.

    Returns:
        The modified TS packet.
    """
    dts = read_dts(ts_packet)
    if dts is None:
        return ts_packet

    modified = bytearray(ts_packet)
    payload_start = modified.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_packet

    # Fix flags: ensure PTS+DTS (0b11) is set, and marker bits preserved
    pes_flags_offset = payload_start + 7
    pes_flags = modified[pes_flags_offset]
    pes_flags = (pes_flags & 0x3F) | 0xC0  # Set PTS+DTS (0b11 << 6)
    modified[pes_flags_offset] = pes_flags

    # Fix PES header length if needed (should be at least 10 for PTS+DTS)
    pes_header_len_offset = payload_start + 8
    pes_header_len = modified[pes_header_len_offset]
    if pes_header_len < 10:
        modified[pes_header_len_offset] = 10

    # Write new DTS
    new_dts_bytes = encode_pts_dts(new_dts, 0b01)
    modified[payload_start + 14: payload_start + 19] = new_dts_bytes
    return bytes(modified)


def read_pcr(ts_packet: bytes):
    """
    Reads the Program Clock Reference (PCR) from a TS packet.

    Args:
        ts_packet: A 188-byte MPEG-TS packet.

    Returns:
        A tuple of (pcr_base, pcr_extension), or None if not present.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        return None

    adaptation_field_control = (ts_packet[3] >> 4) & 0x3
    if adaptation_field_control not in {2, 3}:
        return None

    adaptation_field_length = ts_packet[4]
    # Must be at least 7 bytes for PCR
    if adaptation_field_length < 7:
        return None

    # PCR flag must be set
    if not ts_packet[5] & 0x10:
        return None

    # Reserved bits in PCR (bits 6-1 of byte 10) must be 0x7E (111111)
    if (ts_packet[10] & 0x7E) != 0x7E:
        return None

    # PCR is 6 bytes: base (33 bits), reserved (6 bits), extension (9 bits)
    pcr_base = (
        (ts_packet[6] << 25)
        | (ts_packet[7] << 17)
        | (ts_packet[8] << 9)
        | (ts_packet[9] << 1)
        | (ts_packet[10] >> 7)
    )
    pcr_ext = ((ts_packet[10] & 0x01) << 8) | ts_packet[11]
    return (pcr_base, pcr_ext)


def write_pcr(ts_packet: bytes, new_pcr: int | tuple[int, int]) -> bytes:  # pylint: disable=unsupported-binary-operation
    """
    Writes a new PCR value into a TS packet's adaptation field.

    If the packet does not have an adaptation field, one will be created.

    Args:
        ts_packet: The original TS packet.
        new_pcr: The new PCR value, either as an integer (base only) or a
                 tuple of (base, extension).

    Returns:
        The modified TS packet.
    """
    if len(ts_packet) != TS_PACKET_SIZE:
        return ts_packet

    adaptation_field_control = (ts_packet[3] >> 4) & 0x3
    modified = bytearray(ts_packet)

    # If no adaptation field, add one (set adaptation_field_control to 3, insert field)
    if adaptation_field_control == 1:
        # Only payload present, need to add adaptation field
        modified[3] = (modified[3] & 0xCF) | 0x20  # set adaptation_field_control to 3
        modified.insert(4, 0)  # adaptation_field_length placeholder
        modified.insert(5, 0)  # flags placeholder
        # Stuffing to keep packet size
        while len(modified) < TS_PACKET_SIZE:
            modified.append(0xFF)
        adaptation_field_control = 3

    if adaptation_field_control in {2, 3}:
        adaptation_field_length = modified[4]
        # If adaptation field too short, expand it to at least 7 bytes
        if adaptation_field_length < 7:
            # Insert extra bytes after flags to reach 7
            extra = 7 - adaptation_field_length
            # Insert after flags (at offset 5)
            for _ in range(extra):
                modified.insert(6, 0xFF)
            adaptation_field_length = 7
            modified[4] = adaptation_field_length
            # If packet too long, trim
            if len(modified) > TS_PACKET_SIZE:
                modified = modified[:TS_PACKET_SIZE]

        # Set PCR flag
        modified[5] |= 0x10

        # Write PCR at offset 6
        if isinstance(new_pcr, tuple):
            pcr_base, pcr_ext = new_pcr
        else:
            pcr_base = new_pcr
            pcr_ext = 0
        modified[6] = (pcr_base >> 25) & 0xFF
        modified[7] = (pcr_base >> 17) & 0xFF
        modified[8] = (pcr_base >> 9) & 0xFF
        modified[9] = (pcr_base >> 1) & 0xFF
        modified[10] = ((pcr_base & 0x1) << 7) | 0x7E | ((pcr_ext >> 8) & 0x01)
        modified[11] = pcr_ext & 0xFF
        return bytes(modified)
    return ts_packet


def shift_pts(ts_packet: bytes, offset: int) -> bytes:
    """
    Shifts the PTS value in a single TS packet by a given offset.

    Args:
        ts_packet: The original TS packet.
        offset: The value to add to the PTS.

    Returns:
        The modified TS packet.
    """
    pts = read_pts(ts_packet)
    if pts is None:
        return ts_packet

    new_pts = pts + offset
    if new_pts == pts:
        # No change needed
        return ts_packet

    # Only update the PTS field, preserve all other header fields and flags
    modified = bytearray(ts_packet)
    payload_start = modified.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_packet

    # Write new PTS (do not touch flags or header length)
    new_pts_bytes = encode_pts_dts(new_pts, 0b10)
    modified[payload_start + 9: payload_start + 14] = new_pts_bytes
    return bytes(modified)


def shift_dts(ts_packet: bytes, offset: int) -> bytes:
    """
    Shifts the DTS value in a single TS packet by a given offset.

    Args:
        ts_packet: The original TS packet.
        offset: The value to add to the DTS.

    Returns:
        The modified TS packet.
    """
    dts = read_dts(ts_packet)
    if dts is None:
        return ts_packet

    new_dts = dts + offset
    if new_dts == dts:
        # No change needed
        return ts_packet

    modified = bytearray(ts_packet)
    payload_start = modified.find(b"\x00\x00\x01")
    if payload_start == -1:
        return ts_packet

    # Get original DTS bytes
    orig_dts_bytes = modified[payload_start + 14: payload_start + 19]
    new_dts_bytes = encode_pts_dts_preserve(new_dts, orig_dts_bytes)

    if orig_dts_bytes == new_dts_bytes:
        # No change needed, and encoding matches
        return ts_packet
    # Write new DTS, preserving marker/reserved bits
    modified[payload_start + 14: payload_start + 19] = new_dts_bytes
    return bytes(modified)


def shift_pcr(ts_packet: bytes, offset: int) -> bytes:
    """
    Shifts the PCR value in a single TS packet by a given offset.

    Args:
        ts_packet: The original TS packet.
        offset: The value to add to the PCR base.

    Returns:
        The modified TS packet.
    """
    pcr = read_pcr(ts_packet)
    if pcr is None:
        return ts_packet
    base, ext = pcr
    return write_pcr(ts_packet, (base + offset, ext))


def shift_ts_packet(ts_packet: bytes, offset: int) -> bytes:
    """
    Shifts all relevant timestamps (PTS, DTS, PCR) in a single TS packet.

    Args:
        ts_packet: The original TS packet.
        offset: The value to add to the timestamps.

    Returns:
        The modified TS packet.
    """
    # logger.debug(">>> Shifting TS packet by offset=%s, pcr=%s", offset, pcr)

    pts = read_pts(ts_packet)
    if pts is not None:
        # logger.debug(">>> PTS before shift: %s", pts1)
        ts_packet = shift_pts(ts_packet, offset)

    dts = read_dts(ts_packet)
    if dts is not None:
        # logger.debug(">>> DTS before shift: %s", dts1)
        ts_packet = shift_dts(ts_packet, offset)

    pcr1 = read_pcr(ts_packet)
    if pcr1 is not None:
        # logger.debug(">>> PCR before shift: %s", pcr1)
        ts_packet = shift_pcr(ts_packet, offset)

    return ts_packet


def shift_ts_packet_test(ts_packet: bytes, offset: int) -> bytes:
    """
    A test variant of shift_ts_packet for debugging purposes.

    This function includes extra logging to track timestamp changes and
    verify that the packet modification is correct.

    Args:
        ts_packet: The original TS packet.
        offset: The value to add to the timestamps.

    Returns:
        The modified TS packet.
    """
    # logger.debug(">>> Shifting TS packet by offset=%s, pcr=%s", offset, pcr)
    # logger.debug("TS packet before shift: %s", ts_packet.hex())

    pts1 = read_pts(ts_packet)
    if pts1 is not None:
        # logger.debug(">>> PTS before shift: %s", pts1)
        ts_packet2 = shift_pts(ts_packet, offset)
        _pts2 = read_pts(ts_packet2)
        # logger.debug(">>> PTS after shift: %s", pts2)
        # if pts2 != pts1:
        #     logger.error(">>> PTS changed after shift: %s -> %s", pts1, pts2)
        if ts_packet != ts_packet2:
            logger.error(">>> TS packet changed after PTS shift: %s -> %s", ts_packet.hex(), ts_packet2.hex())
        ts_packet = ts_packet2

    dts1 = read_dts(ts_packet)
    if dts1 is not None:
        # logger.debug(">>> DTS before shift: %s", dts1)
        ts_packet2 = shift_dts(ts_packet, offset)
        _dts2 = read_dts(ts_packet2)
        # logger.debug(">>> DTS after shift: %s", dts2)
        # if dts2 != dts1:
        #     logger.error(">>> DTS changed after shift: %s -> %s", dts1, dts2)
        if ts_packet != ts_packet2:
            logger.error(">>> TS packet changed after DTS shift: %s -> %s", ts_packet.hex(), ts_packet2.hex())
        ts_packet = ts_packet2

    pcr1 = read_pcr(ts_packet)
    if pcr1 is not None:
        # logger.debug(">>> PCR before shift: %s", pcr1)
        ts_packet2 = shift_pcr(ts_packet, pcr1)
        if ts_packet != ts_packet2:
            logger.error(">>> TS packet changed after PCR shift: %s -> %s", ts_packet.hex(), ts_packet2.hex())
        ts_packet = ts_packet2
    # logger.debug(">>> TS packet after shift: %s", ts_packet.hex())
    return ts_packet


def extract_frame_rate_from_segment_data(segment_data: bytes):
    """
    Extracts the frame rate from TS segment data using ffprobe.

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        The frame rate as a float, or None if not found.
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
            logger.debug("FFprobe failed with error: %s", result.stderr)
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
            logger.debug("No video stream found")
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
            logger.debug("Frame rate not available in stream metadata")
            return None

        return frame_rate

    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("Error running FFprobe: %s", e)
        return None
    finally:
        # Clean up temporary file
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass


def extract_resolution_from_segment_data(segment_data: bytes):
    """
    Extracts video resolution by parsing the H.264 SPS NAL unit.

    This function avoids using ffprobe for performance, directly parsing the
    bitstream to find the Sequence Parameter Set (SPS) and read the
    picture width and height.

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple of (width, height), or (None, None) if not found.
    """
    video_pid = find_video_pid_in_segment(segment_data)
    if video_pid is None:
        return None, None

    # Helper to get payload from TS packet
    def get_payload(packet):
        adaptation = (packet[3] >> 4) & 0x3
        offset = 4
        if adaptation in {2, 3}:
            offset += 1 + packet[4]
        return packet[offset:]

    # Collect PES payloads for video PID
    pes_buffer = bytearray()
    for i in range(0, len(segment_data), TS_PACKET_SIZE):
        pkt = segment_data[i:i + TS_PACKET_SIZE]
        if len(pkt) != TS_PACKET_SIZE or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != video_pid:
            continue
        payload = get_payload(pkt)
        pes_buffer.extend(payload)
        # Search for SPS NAL unit (type 7)
        i2 = 0
        while i2 < len(pes_buffer) - 4:
            if pes_buffer[i2:i2 + 3] == b'\x00\x00\x01':
                nal_type = pes_buffer[i2 + 3] & 0x1F
                if nal_type == 7:
                    sps_start = i2 + 4
                    sps_end = sps_start
                    # Find end of NAL (next start code)
                    for j in range(sps_start, len(pes_buffer) - 3):
                        if pes_buffer[j:j + 3] == b'\x00\x00\x01':
                            sps_end = j
                            break
                    else:
                        sps_end = len(pes_buffer)
                    sps_bytes = pes_buffer[sps_start:sps_end]
                    # Parse SPS for width/height
                    return parse_h264_sps_resolution(sps_bytes)
            i2 += 1
    return None, None


def parse_h264_sps_resolution(sps_bytes: bytes):
    """
    Parses H.264 Sequence Parameter Set (SPS) bytes to extract resolution.

    This is a low-level bitstream parsing function that reads the SPS structure
    to calculate the video's width and height.

    Args:
        sps_bytes: The bytes of the SPS NAL unit.

    Returns:
        A tuple of (width, height), or (None, None) if parsing fails.
    """
    # Bitstream reader
    class BitReader:
        def __init__(self, data):
            self.data = data
            self.pos = 0
            self.bit = 0

        def read_bits(self, n):
            val = 0
            for _ in range(n):
                if self.pos >= len(self.data):
                    return None
                val <<= 1
                val |= (self.data[self.pos] >> (7 - self.bit)) & 1
                self.bit += 1
                if self.bit == 8:
                    self.bit = 0
                    self.pos += 1
            return val

        def read_bit(self):
            return self.read_bits(1)

        def read_ue(self):
            zeros = 0
            while True:
                b = self.read_bit()
                if b is None:
                    return None
                if b == 0:
                    zeros += 1
                else:
                    break
            val = 1 << zeros
            val -= 1
            if zeros:
                val += self.read_bits(zeros)
            return val

        def read_se(self):
            ue = self.read_ue()
            if ue is None:
                return None
            if ue % 2 == 0:
                return -(ue // 2)
            return (ue + 1) // 2

    br = BitReader(sps_bytes)
    try:
        br.read_bits(8)  # profile_idc
        br.read_bits(8)  # constraint flags + level_idc
        br.read_ue()     # seq_parameter_set_id
        profile_idc = sps_bytes[0]
        if profile_idc in {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134}:
            chroma_format_idc = br.read_ue()
            if chroma_format_idc == 3:
                br.read_bit()  # separate_colour_plane_flag
            br.read_ue()   # bit_depth_luma_minus8
            br.read_ue()   # bit_depth_chroma_minus8
            br.read_bit()  # qpprime_y_zero_transform_bypass_flag
            seq_scaling_matrix_present_flag = br.read_bit()
            if seq_scaling_matrix_present_flag:
                for _i in range(8 if chroma_format_idc == 3 else 12):
                    if br.read_bit():
                        # skip scaling list
                        pass
        br.read_ue()  # log2_max_frame_num_minus4
        pic_order_cnt_type = br.read_ue()
        if pic_order_cnt_type == 0:
            br.read_ue()  # log2_max_pic_order_cnt_lsb_minus4
        elif pic_order_cnt_type == 1:
            br.read_bit()  # delta_pic_order_always_zero_flag
            br.read_se()   # offset_for_non_ref_pic
            br.read_se()   # offset_for_top_to_bottom_field
            num_ref_frames_in_pic_order_cnt_cycle = br.read_ue()
            for _ in range(num_ref_frames_in_pic_order_cnt_cycle):
                br.read_se()
        br.read_ue()   # max_num_ref_frames
        br.read_bit()  # gaps_in_frame_num_value_allowed_flag
        pic_width_in_mbs_minus1 = br.read_ue()
        pic_height_in_map_units_minus1 = br.read_ue()
        frame_mbs_only_flag = br.read_bit()
        if not frame_mbs_only_flag:
            br.read_bit()  # mb_adaptive_frame_field_flag
        br.read_bit()      # direct_8x8_inference_flag
        frame_cropping_flag = br.read_bit()
        crop_left = crop_right = crop_top = crop_bottom = 0
        if frame_cropping_flag:
            crop_left = br.read_ue()
            crop_right = br.read_ue()
            crop_top = br.read_ue()
            crop_bottom = br.read_ue()
        width = ((pic_width_in_mbs_minus1 + 1) * 16) - (crop_left + crop_right) * 2
        height = (pic_height_in_map_units_minus1 + 1) * 16
        if not frame_mbs_only_flag:
            height *= 2
        height -= (crop_top + crop_bottom) * 2
        return width, height
    except Exception:
        return None, None

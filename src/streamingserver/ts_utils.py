# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
MPEG-TS (Transport Stream) Utilities

This module provides a set of low-level utilities for parsing, analyzing,
and manipulating MPEG-TS packets and segments.
"""

from debug import get_logger

logger = get_logger(__file__)

TS_PACKET_SIZE = 188


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
        logger.debug("Segment too short or empty: %s", len(segment_data))
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
        logger.debug("Sync byte check failed: %s/%s valid packets (need >= %s)", sync_count, total_packets, min_sync_packets)

    # Find majority video PID
    video_pids = [pid for pid in pid_counter if pid == 256 or (0x100 <= pid <= 0x1FF)]
    video_pid_count = sum(pid_counter[pid] for pid in video_pids)
    majority_video_pid = video_pid_count >= int(0.5 * sync_count) and video_pid_count > 0
    if not majority_video_pid:
        logger.debug("Video PID check failed: %s/%s packets with video PID (need >= %s)", video_pid_count, sync_count, int(0.5 * sync_count))

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
        logger.debug("PTS/DTS check failed: %s/%s video packets with valid PTS/DTS (need >= 1)", pts_dts_valid_count, video_pid_count)

    logger.debug("Segment PIDs: %s, video_pid_count=%s, sync_count=%s, pts_dts_valid_count=%s", sorted(pid_counter.keys()), video_pid_count, sync_count, pts_dts_valid_count)
    if corrupted_packets:
        logger.debug("Corrupted TS packets in segment:")
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


def write_pcr(ts_packet: bytes, new_pcr: int | tuple[int, int]) -> bytes:
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

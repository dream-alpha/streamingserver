TS_PACKET_SIZE = 188
 

def segment_contains_pat_pmt(segment_data):
    """
    Returns True if the segment contains both PAT and PMT tables.
    PAT is always PID 0. PMT PID is found in the PAT.
    """
    pat_found = False
    pmt_found = False
    pmt_pid = None
    for i in range(0, len(segment_data), TS_PACKET_SIZE):
        packet = segment_data[i:i + TS_PACKET_SIZE]
        if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        payload_unit_start = (packet[1] & 0x40) != 0
        if pid == 0 and payload_unit_start:
            # PAT
            pointer_field = packet[4]
            pat_start = 5 + pointer_field
            if pat_start + 8 > TS_PACKET_SIZE:
                continue
            table_id = packet[pat_start]
            if table_id != 0x00:
                continue
            section_length = ((packet[pat_start + 1] & 0x0F) << 8) | packet[pat_start + 2]
            program_info_start = pat_start + 8
            program_info_end = pat_start + 3 + section_length - 4  # minus CRC
            i2 = program_info_start
            while i2 + 4 <= program_info_end:
                program_number = (packet[i2] << 8) | packet[i2 + 1]
                if program_number != 0:
                    pmt_pid = ((packet[i2 + 2] & 0x1F) << 8) | packet[i2 + 3]
                    pat_found = True
                    break
                i2 += 4
        elif pmt_pid is not None and pid == pmt_pid and payload_unit_start:
            # PMT
            pointer_field = packet[4]
            pmt_start = 5 + pointer_field
            if pmt_start + 8 > TS_PACKET_SIZE:
                continue
            table_id = packet[pmt_start]
            if table_id != 0x02:
                continue
            pmt_found = True
        if pat_found and pmt_found:
            return True
    return False


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
        pkt = segment_data[i:i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        pid_counter[pid] = pid_counter.get(pid, 0) + 1
    has_valid_sync = sync_count >= 3
    video_pid_found = any(pid == 256 or (0x100 <= pid <= 0x1FF) for pid in pid_counter)
    print(f"[TS_ANALYZE] Segment PIDs: {sorted(pid_counter.keys())}, video_pid_found={video_pid_found}, sync_count={sync_count}")
    return has_valid_sync  # and video_pid_found



def normalize_ts_timestamp(segment_data: bytes, extinf_length: float, current_time: int, global_normalize: bool = False, force_synthesize: bool = False):
    new_data = bytearray(segment_data)
    print(f"[TS_DEBUG] Starting normalize_ts_timestamp: extinf_length={extinf_length}, current_time={current_time}, segment_data_len={len(segment_data)}, global_normalize={global_normalize}")

    first_pts = None
    first_pcr = None
    max_pts = 0
    base_time = current_time
    # For global normalization, keep static offsets across all segments
    if global_normalize:
        if not hasattr(normalize_ts_timestamp, "_global_pts"):
            normalize_ts_timestamp._global_pts = 0
        if not hasattr(normalize_ts_timestamp, "_global_pcr"):
            normalize_ts_timestamp._global_pcr = 0
        global_base_pts = normalize_ts_timestamp._global_pts
        global_base_pcr = normalize_ts_timestamp._global_pcr

    def parse_pts(data):
        pts = ((data[0] >> 1) & 0x07) << 30
        pts |= (((data[1] << 8) | data[2]) >> 1) << 15
        pts |= ((data[3] << 8) | data[4]) >> 1
        return pts

    def build_pts(pts, prefix=0x2):
        pts &= ((1 << 33) - 1)
        b = bytearray(5)
        b[0] = (prefix << 4) | (((pts >> 30) & 0x07) << 1) | 0x01
        b[1] = (pts >> 22) & 0xFF
        b[2] = (((pts >> 15) & 0x7F) << 1) | 0x01
        b[3] = (pts >> 7) & 0xFF
        b[4] = ((pts & 0x7F) << 1) | 0x01
        return b

    def parse_pcr(data):
        if len(data) < 6:
            return 0
        pcr_base = (
            (data[0] << 25) | (data[1] << 17) |
            (data[2] << 9) | (data[3] << 1) | (data[4] >> 7)
        )
        return pcr_base

    def build_pcr(pcr_base):
        pcr_base &= ((1 << 33) - 1)  # Ensure 33-bit value
        pcr = bytearray(6)
        pcr[0] = (pcr_base >> 25) & 0xFF
        pcr[1] = (pcr_base >> 17) & 0xFF
        pcr[2] = (pcr_base >> 9) & 0xFF
        pcr[3] = (pcr_base >> 1) & 0xFF
        pcr[4] = ((pcr_base & 0x01) << 7) | 0x7E  # reserved
        pcr[5] = 0x00
        return pcr


    # Pass 1: Find first_pts and first_pcr if needed
    for i in range(0, len(new_data), TS_PACKET_SIZE):
        packet = new_data[i:i+TS_PACKET_SIZE]
        if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
            continue

        payload_unit_start = (packet[1] & 0x40) != 0
        adaptation_field_control = (packet[3] >> 4) & 0x03
        has_adaptation = adaptation_field_control & 0x2
        has_payload = adaptation_field_control & 0x1

        # PCR
        if has_adaptation:
            adaptation_len = packet[4]
            if adaptation_len >= 7 and len(packet) > 11:
                flags = packet[5]
                if flags & 0x10:
                    pcr_offset = 6
                    if pcr_offset + 6 <= len(packet):
                        pcr = parse_pcr(packet[pcr_offset:pcr_offset+6])
                        if first_pcr is None and pcr != 0:
                            first_pcr = pcr

        # PTS
        if has_payload and payload_unit_start:
            pes_start = 4
            if has_adaptation:
                pes_start += 1 + packet[4]
            if (pes_start + 9 < TS_PACKET_SIZE and 
                pes_start + 2 < len(packet) and 
                packet[pes_start:pes_start+3] == b'\x00\x00\x01'):
                if pes_start + 8 < len(packet):
                    flags = packet[pes_start + 7]
                    pts_dts_flags = (flags >> 6) & 0x03
                    pts_start = pes_start + 9
                    if pts_dts_flags and pts_start + 5 <= len(packet):
                        pts = parse_pts(packet[pts_start:pts_start+5])
                        if first_pts is None and pts != 0:
                            first_pts = pts

    # Synthesize strictly increasing PTS/PCR if missing, always zero, or forced (e.g. for repeated/filler segments)
    synthesize_pts = False
    synthesize_pcr = False
    if global_normalize:
        if first_pts is None or first_pts == 0 or force_synthesize:
            synthesize_pts = True
            print("[TS_WARN] Synthesizing strictly increasing PTS for segment (no valid PTS found, always zero, or forced)")
        if first_pcr is None or first_pcr == 0 or force_synthesize:
            synthesize_pcr = True
            print("[TS_WARN] Synthesizing strictly increasing PCR for segment (no valid PCR found, always zero, or forced)")

    # If current_time is None, set base_time to first_pts or first_pcr (prefer PTS)
    if base_time is None:
        if first_pts is not None:
            print(f"[TS_DEBUG] Initial timestamp normalization: first_pts={first_pts}, setting base_time=0")
            base_time = 0
        elif first_pcr is not None:
            print(f"[TS_DEBUG] Initial timestamp normalization: first_pcr={first_pcr}, setting base_time=0")
            base_time = 0
        else:
            print(f"[TS_DEBUG] Initial timestamp normalization: No valid PTS/PCR found, base_time=0")
            base_time = 0


    # Pass 2: Actually rewrite timestamps
    # For synthesized PTS/PCR, increment by a fixed amount per packet
    synth_pts_val = global_base_pts if global_normalize else base_time
    synth_pcr_val = global_base_pcr if global_normalize else base_time
    # Calculate per-packet increment, always at least 1
    num_packets = max(1, (len(new_data) // TS_PACKET_SIZE))
    # Use (duration * 90000 - 1) to ensure last PTS is just before next segment, not after
    pts_increment = max(1, int((extinf_length * 90000 - 1) / num_packets))
    pcr_increment = pts_increment

    for i in range(0, len(new_data), TS_PACKET_SIZE):
        packet = new_data[i:i+TS_PACKET_SIZE]
        if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
            continue

        payload_unit_start = (packet[1] & 0x40) != 0
        adaptation_field_control = (packet[3] >> 4) & 0x03
        has_adaptation = adaptation_field_control & 0x2
        has_payload = adaptation_field_control & 0x1

        # PCR
        if has_adaptation:
            adaptation_len = packet[4]
            if adaptation_len >= 7 and len(packet) > 11:
                flags = packet[5]
                if flags & 0x10:
                    pcr_offset = 6
                    if pcr_offset + 6 <= len(packet):
                        pcr = parse_pcr(packet[pcr_offset:pcr_offset+6])
                        if global_normalize and synthesize_pcr:
                            pcr_shifted = synth_pcr_val
                            synth_pcr_val += pcr_increment
                        elif global_normalize:
                            if first_pcr is not None:
                                pcr_shifted = global_base_pcr + (pcr - first_pcr)
                            else:
                                pcr_shifted = global_base_pcr
                        else:
                            if first_pcr is not None:
                                pcr_shifted = base_time + (pcr - first_pcr)
                            else:
                                pcr_shifted = base_time
                        packet[pcr_offset:pcr_offset+6] = build_pcr(pcr_shifted)

        # PTS/DTS
        if has_payload and payload_unit_start:
            pes_start = 4
            if has_adaptation:
                pes_start += 1 + packet[4]
            if (pes_start + 9 < TS_PACKET_SIZE and 
                pes_start + 2 < len(packet) and 
                packet[pes_start:pes_start+3] == b'\x00\x00\x01'):
                if pes_start + 8 < len(packet):
                    flags = packet[pes_start + 7]
                    pts_dts_flags = (flags >> 6) & 0x03
                    pts_start = pes_start + 9
                    if pts_dts_flags and pts_start + 5 <= len(packet):
                        pts = parse_pts(packet[pts_start:pts_start+5])
                        if global_normalize and synthesize_pts:
                            new_pts = synth_pts_val
                            synth_pts_val += pts_increment
                        elif global_normalize:
                            if first_pts is not None:
                                new_pts = global_base_pts + (pts - first_pts)
                            else:
                                new_pts = global_base_pts
                        else:
                            if first_pts is not None:
                                new_pts = base_time + (pts - first_pts)
                            else:
                                new_pts = base_time
                        max_pts = max(max_pts, new_pts)
                        packet[pts_start:pts_start+5] = build_pts(new_pts)

                        if pts_dts_flags == 0x3:  # PTS + DTS
                            dts_start = pts_start + 5
                            if dts_start + 5 <= len(packet):
                                if global_normalize and synthesize_pts:
                                    # For synthesized, DTS = PTS (no extra increment)
                                    new_dts = new_pts
                                elif global_normalize:
                                    dts = parse_pts(packet[dts_start:dts_start+5])
                                    if first_pts is not None:
                                        new_dts = global_base_pts + (dts - first_pts)
                                    else:
                                        new_dts = global_base_pts
                                else:
                                    dts = parse_pts(packet[dts_start:dts_start+5])
                                    if first_pts is not None:
                                        new_dts = base_time + (dts - first_pts)
                                    else:
                                        new_dts = base_time
                                packet[dts_start:dts_start+5] = build_pts(new_dts, prefix=0x1)

    # Advance time for next segment
    if global_normalize:
        seg_duration = int(extinf_length * 90000)
        normalize_ts_timestamp._global_pts += seg_duration
        normalize_ts_timestamp._global_pcr += seg_duration
        new_time = normalize_ts_timestamp._global_pts
    else:
        new_time = base_time + int(extinf_length * 90000)
    if max_pts == 0 and first_pcr is None:
        print("[TS_DEBUG] No PTS/DTS/PCR found in this segment, but segment is valid TS.")
    print(f"[TS_DEBUG] Finished normalize_ts_timestamp: new_time={new_time}, first_pts={first_pts}, first_pcr={first_pcr}, max_pts={max_pts}")
    return bytes(new_data), new_time

def segment_contains_keyframe(segment_data):
    """
    Returns True if the segment contains a video keyframe (IDR).
    Only works for H.264/AVC streams.
    """
    TS_PACKET_SIZE = 188
    video_pids = set()
    # First, find likely video PIDs (reuse logic from is_valid_ts_segment)
    for i in range(0, min(len(segment_data), 188 * 20), 188):
        pkt = segment_data[i:i + 188]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        # Heuristic: video PIDs are usually 0x100-0x1FF or 256
        if pid == 256 or (0x100 <= pid <= 0x1FF):
            video_pids.add(pid)
    # Now, scan for IDR NAL units in video PID packets
    for i in range(0, len(segment_data), TS_PACKET_SIZE):
        pkt = segment_data[i:i + TS_PACKET_SIZE]
        if len(pkt) < 188 or pkt[0] != 0x47:
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid not in video_pids:
            continue
        payload_unit_start = (pkt[1] & 0x40) != 0
        adaptation_field_control = (pkt[3] >> 4) & 0x03
        has_adaptation = adaptation_field_control & 0x2
        has_payload = adaptation_field_control & 0x1
        payload_start = 4
        if has_adaptation:
            payload_start += 1 + pkt[4]
        if not has_payload or payload_start >= TS_PACKET_SIZE:
            continue
        payload = pkt[payload_start:]
        # Look for PES start code
        pes_offset = 0
        if payload_unit_start and payload[:3] == b'\x00\x00\x01':
            pes_offset = 9 + ((payload[8] if len(payload) > 8 else 0))
        else:
            pes_offset = 0
        es_data = payload[pes_offset:]
        # Scan for NAL unit start codes (0x000001 or 0x00000001)
        i2 = 0
        while i2 < len(es_data) - 4:
            if es_data[i2:i2+3] == b'\x00\x00\x01':
                nal_start = i2 + 3
            elif es_data[i2:i2+4] == b'\x00\x00\x00\x01':
                nal_start = i2 + 4
            else:
                i2 += 1
                continue
            if nal_start < len(es_data):
                nal_type = es_data[nal_start] & 0x1F
                if nal_type == 5:  # IDR frame
                    return True
            i2 = nal_start
    return False

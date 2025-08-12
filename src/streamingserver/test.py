#!/usr/bin/env python3
"""
TS Timestamp Analyzer
Analyzes a recorded TS file and prints PTS, DTS, PCR values and their differences between packets and segments.
"""
import sys
from debug import get_logger

logger = get_logger(__file__)


def parse_ts_packets(ts_data):
    packets = []
    packet_size = 188
    for i in range(0, len(ts_data), packet_size):
        packet = ts_data[i:i + packet_size]
        if len(packet) == packet_size:
            packets.append(packet)
    return packets


def get_pcr(packet):
    # PCR is in adaptation field if present
    if packet[3] & 0x20:
        adaptation_field_length = packet[4]
        if adaptation_field_length >= 7 and packet[5] & 0x10:
            # PCR is 6 bytes at packet[6:12]
            pcr_bytes = packet[6:12]
            if len(pcr_bytes) == 6:
                pcr_base = (
                    (pcr_bytes[0] << 25)
                    | (pcr_bytes[1] << 17)
                    | (pcr_bytes[2] << 9)
                    | (pcr_bytes[3] << 1)
                    | (pcr_bytes[4] >> 7)
                )
                return pcr_base
    return None


def get_pts_dts(packet):
    # Look for PES header
    if packet[3] & 0x10:
        payload_start = 4
        if packet[3] & 0x20:
            payload_start += packet[4] + 1
        if packet[payload_start:payload_start + 3] == b'\x00\x00\x01':
            _stream_id = packet[payload_start + 3]
            pes_header_data_length = packet[payload_start + 8]
            pts = None
            dts = None
            if pes_header_data_length >= 5:
                pts = (
                    ((packet[payload_start + 9] & 0x0E) << 29)
                    | ((packet[payload_start + 10] & 0xFF) << 22)
                    | ((packet[payload_start + 11] & 0xFE) << 14)
                    | ((packet[payload_start + 12] & 0xFF) << 7)
                    | ((packet[payload_start + 13] & 0xFE) >> 1)
                )
                if pes_header_data_length >= 10:
                    dts = (
                        ((packet[payload_start + 14] & 0x0E) << 29)
                        | ((packet[payload_start + 15] & 0xFF) << 22)
                        | ((packet[payload_start + 16] & 0xFE) << 14)
                        | ((packet[payload_start + 17] & 0xFF) << 7)
                        | ((packet[payload_start + 18] & 0xFE) >> 1)
                    )
            return pts, dts
    return None, None


def analyze_ts_file(ts_path):
    with open(ts_path, 'rb') as f:
        ts_data = f.read()
    packets = parse_ts_packets(ts_data)
    last_pts = last_dts = last_pcr = None
    logger.debug("Idx\tPID\tPTS\tDTS\tPCR\tPTS_diff\tDTS_diff\tPCR_diff")
    for idx, packet in enumerate(packets):
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        pts, dts = get_pts_dts(packet)
        pcr = get_pcr(packet)
        pts_diff = dts_diff = pcr_diff = None
        # Use last non-None value for diff calculation
        if pts is not None:
            if last_pts is not None:
                pts_diff = pts - last_pts
            last_pts = pts
        if dts is not None:
            if last_dts is not None:
                dts_diff = dts - last_dts
            last_dts = dts
        if pcr is not None:
            if last_pcr is not None:
                pcr_diff = pcr - last_pcr
            last_pcr = pcr
        # Only print if at least one of PTS, DTS, PCR is present
        if pts is not None or dts is not None or pcr is not None:
            logger.debug(f"{idx}\t{pid}\t{pts}\t{dts}\t{pcr}\t{pts_diff}\t{dts_diff}\t{pcr_diff}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.debug("Usage: python3 test.py <ts_file>")
        sys.exit(1)
    analyze_ts_file(sys.argv[1])

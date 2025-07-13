import unittest
from ts_timestamp_utils import read_pts_from_segment, TS_PACKET_SIZE

def make_ts_packet_with_pts(pts):
    # Minimal MPEG-TS packet with sync byte and fake PES header with PTS
    packet = bytearray([0x47, 0x00, 0x00, 0x10])
    packet += bytearray([0x00] * (TS_PACKET_SIZE - 4))
    # Insert PES header at offset 4
    pes_header = bytearray(b'\x00\x00\x01\xe0')
    pes_header += bytearray([0x00, 0x00, 0x80, 0x80, 0x05])
    # Encode PTS
    def encode_pts(val):
        return bytes([
            0x21 | ((val >> 29) & 0x0E),
            (val >> 22) & 0xFF,
            0x01 | ((val >> 14) & 0xFE),
            (val >> 7) & 0xFF,
            0x01 | ((val << 1) & 0xFE)
        ])
    pes_header += encode_pts(pts)
    packet[4:4+len(pes_header)] = pes_header
    return bytes(packet)

class TestReadPtsFromSegment(unittest.TestCase):
    def test_pts_in_middle(self):
    # Test suite for read_pts_from_segment:
    # Each test describes the packet layout, which packets contain PTS, and what is expected for first_pts, last_pts, and ts_packet_duration.
    # Interpolation logic is explained in comments for each test.
        # PTS only in the middle packet, segment of 5 packets
        # Should interpolate first and last PTS using average duration (which is 0)
        # Only the middle packet contains PTS (10000), all others are empty.
        # Should return first_pts == last_pts == 10000
        # ts_packet_duration == 0 (no interval to average)
        packets = [bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(10000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1))]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        self.assertAlmostEqual(first_pts, 10000)
        self.assertAlmostEqual(last_pts, 10000)
        self.assertEqual(ts_packet_duration, 0)

    def test_multiple_pts_sparse(self):
        # PTS in first, third, and last packet, segment of 5 packets
        # Should interpolate using average duration between PTS packets
        # PTS in first (1000), third (4000), and last (7000) packet of 5.
        # Should return first_pts == 1000, last_pts == 7000
        # ts_packet_duration == 1500 (average of intervals between PTS packets)
        packets = [make_ts_packet_with_pts(1000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(4000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(7000)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # avg = ((4000-1000)/2 + (7000-4000)/2)/2 = (1500 + 1500)/2 = 1500
        # last_idx = 4, total_packets = 5, so interpolated_last_pts = 7000 + 1500 * (5-1-4) = 7000 + 1500*0 = 7000
        self.assertAlmostEqual(first_pts, 1000)
        self.assertAlmostEqual(last_pts, 7000)
        self.assertAlmostEqual(ts_packet_duration, 1500)

    def test_pts_every_packet(self):
        # PTS in every packet, segment of 4 packets
        # PTS in every packet: 1000, 2000, 3000, 4000.
        # Should return first_pts == 1000, last_pts == 4000
        # ts_packet_duration == 1000 (all intervals are 1000)
        # Should interpolate nothing, duration is average between all
        packets = [make_ts_packet_with_pts(1000),
                   make_ts_packet_with_pts(2000),
                   make_ts_packet_with_pts(3000),
                   make_ts_packet_with_pts(4000)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # avg = ((2000-1000)/1 + (3000-2000)/1 + (4000-3000)/1)/3 = (1000+1000+1000)/3 = 1000
        self.assertAlmostEqual(first_pts, 1000)
        self.assertAlmostEqual(last_pts, 4000)
        self.assertAlmostEqual(ts_packet_duration, 1000)

    def test_pts_gap_at_start(self):
        # Only last two packets contain PTS: 1000, 2000 (packets 2 and 3 of 4).
        # Should interpolate first_pts: 1000 - 1000*2 = -1000
        # last_pts == 2000
        # ts_packet_duration == 1000
        # PTS only in last two packets, segment of 4 packets
        # Should interpolate first_pts
        packets = [bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(1000),
                   make_ts_packet_with_pts(2000)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # avg = (2000-1000)/(3-2) = 1000
        # first_idx = 2, interpolated_first_pts = 1000 - 1000*2 = -1000
        self.assertAlmostEqual(first_pts, -1000)
        self.assertAlmostEqual(last_pts, 2000)
        self.assertAlmostEqual(ts_packet_duration, 1000)
        # Only first two packets contain PTS: 1000, 2000 (packets 0 and 1 of 4).
        # Should interpolate last_pts: 2000 + 1000*2 = 4000
        # first_pts == 1000
        # ts_packet_duration == 1000

    def test_pts_gap_at_end(self):
        # PTS only in first two packets, segment of 4 packets
        # Should interpolate last_pts
        packets = [make_ts_packet_with_pts(1000),
                   make_ts_packet_with_pts(2000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1))]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # avg = (2000-1000)/(1-0) = 1000
        # last_idx = 1, interpolated_last_pts = 2000 + 1000*(4-1-1) = 2000 + 1000*2 = 4000
        # PTS in packets 0 (1000), 1 (2000), and 3 (3000) of 5.
        # Should interpolate last_pts: 3000 + 750*1 = 3750
        # first_pts == 1000
        # ts_packet_duration == 750 (average of 1000/1 and 1000/2)
        self.assertAlmostEqual(first_pts, 1000)
        self.assertAlmostEqual(last_pts, 4000)
        self.assertAlmostEqual(ts_packet_duration, 1000)
    def test_basic_pts_extraction(self):
        # Create segment with 5 packets, PTS in 1st, 2nd, and 4th
        packets = [make_ts_packet_with_pts(1000),
                   make_ts_packet_with_pts(2000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(3000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1))]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # Interpolated first_pts and last_pts
        # Only middle packet contains PTS (5000), all others are empty.
        # Should return first_pts == last_pts == 5000
        # ts_packet_duration == 0
        # avg = (1000/1 + 1000/2)/2 = 750
        # first_idx = 0, last_idx = 3, total_packets = 5
        # interpolated_last_pts = 3000 + 750 * (5-1-3) = 3000 + 750*1 = 3750
        self.assertAlmostEqual(first_pts, 1000)
        self.assertAlmostEqual(last_pts, 3750)
        self.assertAlmostEqual(ts_packet_duration, 750)

    def test_interpolation(self):
        # PTS only in 2nd packet, should not interpolate for first and last
        packets = [bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
                   make_ts_packet_with_pts(5000),
        # No packet contains PTS.
        # Should return None for all outputs.
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1))]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        self.assertAlmostEqual(first_pts, 5000)
        self.assertAlmostEqual(last_pts, 5000)
        self.assertEqual(ts_packet_duration, 0)

    def test_no_pts(self):
        # No PTS in any packet
        # PTS in first (1000) and last (3000) packet of 3.
        # Should return first_pts == 1000, last_pts == 3000
        # ts_packet_duration == 1000
        packets = [bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)) for _ in range(3)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        self.assertIsNone(first_pts)
        self.assertIsNone(last_pts)
        self.assertIsNone(ts_packet_duration)

    def test_pts_at_start_and_end(self):
        # PTS in first and last packet only
        packets = [make_ts_packet_with_pts(1000),
                   bytearray([0x47] + [0x00]* (TS_PACKET_SIZE-1)),
        # Only one packet, with PTS (12345).
        # Should return first_pts == last_pts == 12345
        # ts_packet_duration == 0
                   make_ts_packet_with_pts(3000)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        # avg = (3000-1000)/(2-0) = 1000
        # first_idx = 0, last_idx = 2, total_packets = 3
        # interpolated_last_pts = 3000 + 1000 * (3-1-2) = 3000 + 0 = 3000
        self.assertAlmostEqual(first_pts, 1000)
        self.assertAlmostEqual(last_pts, 3000)
        self.assertAlmostEqual(ts_packet_duration, 1000)
        # Two packets, both with same PTS (5555).
        # Should return first_pts == last_pts == 5555
        # ts_packet_duration == 0

    def test_single_packet_with_pts(self):
        # Only one packet, with PTS
        packets = [make_ts_packet_with_pts(12345)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        self.assertAlmostEqual(first_pts, 12345)
        self.assertAlmostEqual(last_pts, 12345)
        self.assertEqual(ts_packet_duration, 0)

    def test_two_packets_with_same_pts(self):
        # Two packets, both with same PTS
        packets = [make_ts_packet_with_pts(5555), make_ts_packet_with_pts(5555)]
        segment = b''.join(packets)
        first_pts, last_pts, ts_packet_duration = read_pts_from_segment(segment)
        self.assertAlmostEqual(first_pts, 5555)
        self.assertAlmostEqual(last_pts, 5555)
        self.assertEqual(ts_packet_duration, 0)

if __name__ == '__main__':
    unittest.main()

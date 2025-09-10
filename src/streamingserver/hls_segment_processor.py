"""
HLS Segment Processing Utilities

This module provides a function to process all logic for a single HLS segment,
as refactored from hls_recorder.py.
"""

from ts_utils import shift_segment, is_valid_ts_segment, set_discontinuity_segment, update_continuity_counters
from ffmpeg_utils import close_ffmpeg_process, open_ffmpeg_process, write_ffmpeg_segment
from hls_segment_utils import append_to_rec_file, get_segment_properties, download_segment, is_filler_segment
from hls_playlist_utils import different_uris
from log_utils import write_log
from debug import get_logger

logger = get_logger(__file__)


class HLSSegmentProcessor:
    def __init__(self, rec_dir, socketserver):
        self.rec_dir = rec_dir
        self.rec_file = rec_dir + "/pluto.ts"
        self.socketserver = socketserver
        # State variables
        self.segment_index = 0
        self.previous_segment_index = -1
        self.section_index = -1
        self.previous_uri = ""
        self.previous_duration = 0
        self.previous_pts = 0
        self.current_resolution = None
        self.previous_resolution = None
        self.offset = 0
        self.continuous_pts = 0
        self.cc_map = {}
        self.monotonize_segment = False
        self.section_file = ""
        self.ffmpeg_proc = None
        self.previous_filler = None
        self.current_filler = None

    def process_segment(self, session, target_duration, buffering, segment):
        logger.info("Segment: %s: %s", self.segment_index, segment.uri)
        new_section = False
        key_info = {"METHOD": None, "URI": None, "IV": None}
        if segment.key:
            key_info = {"METHOD": segment.key.method, "URI": segment.key.uri, "IV": segment.key.iv}

        segment_data = download_segment(session, segment.uri, self.segment_index, key_info, max_retries=10, timeout=5)
        if not segment_data or not is_valid_ts_segment(segment_data):
            logger.error("Failed to download segment or invalid ts segment %s", self.segment_index)
            return None

        self.current_resolution, current_duration, current_pts, _vpids, _apids = get_segment_properties(segment_data)
        if current_pts is None:
            raise ValueError(f"No PTS found in segment {self.segment_index}")
        if current_duration is None:
            current_duration = target_duration

        if different_uris(self.previous_uri, segment.uri):
            logger.info("Prev URI: %s", self.previous_uri)
            logger.info(">" * 70)
            logger.info("Next URI: %s", segment.uri)
            if self.previous_resolution != self.current_resolution:
                logger.info("Resolution changed: %s > %s", self.previous_resolution, self.current_resolution)
                write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="resolution-change")
                new_section = True

            self.current_filler = is_filler_segment(segment.uri)
            if self.current_filler != self.previous_filler:
                logger.info("Filler changed: current_filler: %s, %s", self.current_filler, segment.uri)
                write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="filler-change")
                new_section = True
            self.monotonize_segment = self.current_filler

        if new_section and self.section_index > 0:
            # check if previous segment was too short and use a filler file instead
            if self.previous_filler and self.previous_segment_index < buffering:
                logger.info("Inserting bumper file before new section")
                write_log(self.rec_dir, self.previous_uri, self.section_index, self.previous_segment_index, msg="bumper-file")
                bumper_file = "/data/ubuntu/root/plugins/streamingserver/data/ad2_0.ts"
                if self.socketserver:
                    self.socketserver.broadcast({"command": "start", "args": [self.previous_uri, bumper_file, self.section_index, -1]})

        if new_section:
            logger.info("=" * 70)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="new-section\n")
            close_ffmpeg_process(self.ffmpeg_proc)
            self.segment_index = 0
            self.section_index += 1
            self.continuous_pts = current_pts
            self.offset = 0
            self.cc_map = {}
            self.section_file = f"{self.rec_dir}/stream_{self.section_index}.ts"
            if not self.current_filler:
                self.ffmpeg_proc = open_ffmpeg_process(self.section_file)
        else:
            self.continuous_pts += self.previous_duration
            self.offset = self.continuous_pts - current_pts

        logger.debug("Timestamps %s: %s, Previous duration: %s,  Current PTS: %s, Continuous PTS: %s, Offset: %s", self.segment_index, self.previous_pts, self.previous_duration, current_pts, self.continuous_pts, self.offset)

        if self.monotonize_segment:
            segment_data = shift_segment(segment_data, self.offset)
            segment_data, self.cc_map = update_continuity_counters(segment_data, self.cc_map)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="monotonize")

        if segment.discontinuity and self.current_filler:
            logger.info("Discontinuity found in segment %s", self.segment_index)
            segment_data = set_discontinuity_segment(segment_data)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="discontinuity")

        if not self.current_filler:
            write_ffmpeg_segment(self.ffmpeg_proc, segment_data)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="ffmpeg-segment")
        else:
            append_to_rec_file(self.section_file, segment_data)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="filler-segment")
        logger.info("Writing segment %s, %s to %s", self.segment_index, segment.uri, self.section_file)

        if self.socketserver:
            self.socketserver.broadcast({"command": "start", "args": [segment.uri, self.section_file, self.section_index, self.segment_index]})

        self.previous_segment_index = self.segment_index
        self.segment_index += 1
        self.previous_uri = segment.uri
        self.previous_duration = current_duration
        self.previous_pts = current_pts
        self.previous_resolution = self.current_resolution
        self.previous_filler = self.current_filler

        return True

# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Segment Processing Utilities

This module provides a function to process all logic for a single HLS segment,
as refactored from hls_recorder.py.
"""

import os
from urllib.parse import urljoin
from ts_utils import shift_segment, is_valid_ts_segment, set_discontinuity_segment, update_continuity_counters
from ffmpeg_utils import close_ffmpeg_process, open_ffmpeg_process, write_ffmpeg_segment
from hls_segment_utils import append_to_rec_file, get_segment_properties, download_segment, is_filler_segment  # , save_segment_to_file
from hls_playlist_utils import different_uris
from log_utils import write_log
from drm_utils import comprehensive_drm_check
from debug import get_logger

logger = get_logger(__file__)


class HLSSegmentProcessor:
    def __init__(self, rec_dir, socketserver, playlist_base_url=None, recorder_id=None):
        self.rec_dir = rec_dir
        self.socketserver = socketserver
        self.playlist_base_url = playlist_base_url
        self.recorder_id = recorder_id or "hls_unknown"
        logger.info("HLSSegmentProcessor initialized with socketserver: %s", socketserver)
        logger.info("Playlist base URL: %s", playlist_base_url)
        logger.info("Recorder type: %s", self.recorder_id)
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
        self.buffering_completed = False

    def _resolve_segment_url(self, segment_uri):
        """
        Resolve segment URI to absolute URL if it's relative.

        Args:
            segment_uri (str): Segment URI from m3u8 playlist (may be relative)

        Returns:
            str: Absolute URL for the segment
        """
        # If segment URI is already absolute, return as-is
        if segment_uri.startswith('http'):
            return segment_uri

        # If we have a base URL, join the relative URI with it
        if self.playlist_base_url:
            resolved_url = urljoin(self.playlist_base_url, segment_uri)
            logger.info("Joined '%s' with base '%s' = '%s'", segment_uri, self.playlist_base_url, resolved_url)
            return resolved_url

        # Fallback: return original URI (will likely fail but better than crashing)
        logger.warning("No base URL available for resolving relative segment URI: %s", segment_uri)
        return segment_uri

    def process_segment(self, session, target_duration, buffering, segment):
        logger.info("Segment: %s: %s", self.segment_index, segment.uri)

        # Resolve relative segment URLs to absolute URLs
        segment_url = self._resolve_segment_url(segment.uri)
        logger.info("Resolved segment URL: %s", segment_url)

        new_section = False
        key_info = {"METHOD": None, "URI": None, "IV": None}
        if segment.key:
            key_info = {"METHOD": segment.key.method, "URI": segment.key.uri, "IV": segment.key.iv}

        segment_data = download_segment(session, segment_url, self.segment_index, key_info, max_retries=10, timeout=5)
        if not segment_data or not is_valid_ts_segment(segment_data):
            logger.error("Failed to download segment or invalid ts segment %s", self.segment_index)

            # Enhanced DRM detection for consistent segment failures
            drm_result = comprehensive_drm_check(
                url=segment_url,
                content="",  # We don't have segment content since download failed
                headers=None,  # Could add response headers if available from download_segment
                error_message="Failed to download segment",
                content_type="ts"
            )

            # Enhanced DRM detection for segment download failures
            enhanced_drm_detected = False
            drm_indicators = []

            # Check for DRM patterns in CloudFront URLs with consistent failures
            if "cloudfront.net" in segment_url.lower() and "/segment-" in segment_url:
                logger.info("CloudFront segment detected with download failure - potential DRM protection")
                enhanced_drm_detected = True
                drm_indicators.append("CloudFront DRM-protected segment pattern")

            # Also check if the segment has encryption key info that suggests DRM
            if key_info and key_info.get("METHOD") and key_info["METHOD"] != "NONE":
                logger.info("Segment has encryption key: METHOD=%s, URI=%s",
                            key_info.get("METHOD"), key_info.get("URI"))

                # Check if this looks like DRM rather than standard AES-128
                # Only flag true DRM systems, not basic AES-128 encryption with public keys
                is_true_drm = False
                if key_info["METHOD"] != "AES-128":
                    # Non-AES-128 methods are likely DRM
                    is_true_drm = True
                    logger.debug(f"DRM detected: Non-AES-128 method: {key_info['METHOD']}")
                elif key_info.get("URI"):
                    # Check for actual DRM license server patterns, excluding false positives
                    uri_lower = str(key_info["URI"]).lower()
                    true_drm_patterns = ("widevine", "playready", "fairplay", "license/", "/drm/", "drmtoday", "axinom")
                    # Exclude PlutoTV-style false positives like "720pDRM" in path
                    false_positive_patterns = ("720pdrm", "1080pdrm", "480pdrm", "/720pdrm/", "/1080pdrm/", "/480pdrm/")

                    has_drm_pattern = any(pattern in uri_lower for pattern in true_drm_patterns)
                    has_false_positive = any(pattern in uri_lower for pattern in false_positive_patterns)

                    logger.debug(f"DRM check: URI={key_info['URI']}, has_drm={has_drm_pattern}, has_false_pos={has_false_positive}")

                    is_true_drm = has_drm_pattern and not has_false_positive

                if is_true_drm:
                    logger.warning("Detected DRM encryption in segment key: %s", key_info)
                    enhanced_drm_detected = True
                    drm_indicators.append(f"Segment encryption key: {key_info['METHOD']}")

            # If DRM is detected (either original or enhanced), raise a specific DRM error
            if drm_result["has_drm"] or enhanced_drm_detected:
                all_indicators = drm_result.get("indicators", []) + drm_indicators
                logger.error("DRM protection detected - segment download failed due to encryption")
                logger.info("DRM indicators: %s", all_indicators)
                drm_message = f"Segment download failed, DRM indicators: {', '.join(all_indicators)}"
                raise ValueError(f"DRM_PROTECTED: {drm_message}")

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

        if new_section and self.buffering_completed:
            # check if previous segment was too short and use a filler file instead
            if self.previous_filler and self.previous_segment_index < 3:
                logger.info("Inserting bumper file before new section")
                write_log(self.rec_dir, "bumper-file", self.section_index, self.previous_segment_index, msg="bumper-file")
                try:
                    bumper_file = "/root/plugins/streamingserver/data/ad2_0.ts"
                    if not os.path.exists(bumper_file):
                        bumper_file = "data/ad2_0.ts"
                    with open(bumper_file, "rb") as bf:
                        bumper_data = bf.read()
                except Exception:
                    logger.error("Failed to read bumper file: %s", bumper_file)
                    bumper_data = b""

                logger.info("Inserting bumper file of size %s bytes", len(bumper_data))
                os.remove(self.section_file)
                logger.info("Removed section file %s to insert bumper", self.section_file)
                append_to_rec_file(self.section_file, bumper_data)
                if self.socketserver:
                    logger.info("Broadcasting bumper file message")
                    self.socketserver.broadcast(["start", {
                        "url": "bumper-file",
                        "rec_file": self.section_file,
                        "section_index": self.section_index,
                        "segment_index": self.previous_segment_index,
                        "recorder_id": self.recorder_id
                    }])
                    logger.info("Bumper file broadcast complete")
                else:
                    logger.warning("No socketserver available for bumper file broadcast")

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

        if segment.discontinuity:
            logger.info("Discontinuity found in segment %s", self.segment_index)
            segment_data = set_discontinuity_segment(segment_data)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="discontinuity")

        if not self.current_filler:
            write_ffmpeg_segment(self.ffmpeg_proc, segment_data)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="ffmpeg-segment")
        else:
            append_to_rec_file(self.section_file, segment_data)
            # self.segment_file = f"{self.rec_dir}/segment_{self.segment_index}.ts"
            # save_segment_to_file(segment_data, self.segment_file)
            write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="filler-segment")

        logger.info("Writing segment %s, %s to %s", self.segment_index, segment.uri, self.section_file)

        if self.segment_index == buffering:
            logger.info("Buffering reached (%d), checking socketserver: %s", buffering, self.socketserver)
            if self.socketserver:
                logger.info("Broadcasting start message for segment %d", self.segment_index)
                self.socketserver.broadcast(["start", {
                    "url": segment.uri,
                    "rec_file": self.section_file,
                    "section_index": self.section_index,
                    "segment_index": self.segment_index,
                    "recorder_id": self.recorder_id
                }])
                logger.info("Broadcast complete")
            else:
                logger.warning("No socketserver available for broadcast")
            if not self.buffering_completed:
                self.buffering_completed = True
                logger.info("Buffering completed.")
                write_log(self.rec_dir, segment.uri, self.section_index, self.segment_index, msg="buffering-complete")

        self.previous_segment_index = self.segment_index
        self.segment_index += 1
        self.previous_uri = segment.uri
        self.previous_duration = current_duration
        self.previous_pts = current_pts
        self.previous_resolution = self.current_resolution
        self.previous_filler = self.current_filler

        return True

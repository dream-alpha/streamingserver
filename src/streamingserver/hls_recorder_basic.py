# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Basic HLS Recorder

This module provides a basic HLS recorder implementation that handles standard
HLS streams. It supports both VOD and live streams with playlist monitoring,
segment processing, and error recovery.
"""

from __future__ import annotations

import time
import traceback
import m3u8
from hls_playlist_utils import get_playlist
from drm_utils import detect_drm_in_content
from ffmpeg_utils import terminate_ffmpeg_process
from log_utils import write_log
from hls_segment_processor import HLSSegmentProcessor
from base_recorder import BaseRecorder
from debug import get_logger

logger = get_logger(__file__)


class HLS_Recorder_Basic(BaseRecorder):
    """
    Basic HLS recorder implementation.

    This class provides a straightforward HLS recording implementation
    that handles standard HLS streams with playlist fetching, segment
    processing, and basic error recovery.
    """

    def __init__(self):
        """Initializes the basic HLS_Recorder instance."""
        super().__init__(self.__class__.__name__)
        self.channel_uri = ""
        self.session = None

    def record_start(self, resolve_result):
        """
        Starts the recording process - BaseRecorder handles threading.

        Args:
            resolve_result (dict): Complete resolve result containing resolved_url,
                                 rec_dir, show_ads, buffering, auth_tokens,
                                 original_url, all_sources, etc.
        """
        super().record_start(resolve_result)  # Ensure parent cleanup
        # Extract data from resolve_result
        self.channel_uri = resolve_result.get("resolved_url")
        self.rec_dir = resolve_result.get("rec_dir", "/tmp")
        self.buffering = resolve_result.get("buffering", 5)
        self.session = resolve_result.get("session")

        if not self.session:
            raise ValueError("No session available for HLS recording")

        logger.info("#" * 70)
        logger.info("Starting HLS recording for channel: %s", self.channel_uri)
        logger.info("#" * 70)

        self.record_stream(self.channel_uri, self.rec_dir, self.buffering)

    def process_playlist_content(self, playlist_text):
        """
        Process playlist content.
        Override this method in specialized recorders for type-specific processing.
        """
        playlist = m3u8.loads(playlist_text)

        # Check for DRM protection in playlist
        if detect_drm_in_content(playlist_text, content_type="m3u8")["has_drm"]:
            raise ValueError("DRM_PROTECTED: Stream uses DRM protection (detected in playlist)")

        return playlist

    def handle_master_playlist(self, playlist, base_url):
        """
        Handle master playlist by selecting the best quality stream.
        Returns the URL of the selected media playlist.
        """
        if not playlist.playlists:
            logger.error("Master playlist has no streams")
            return None

        # Sort streams by bandwidth (quality) - highest first
        sorted_streams = sorted(playlist.playlists, key=lambda x: x.stream_info.bandwidth, reverse=True)

        # Select the highest quality stream
        best_stream = sorted_streams[0]
        resolution = getattr(best_stream.stream_info, 'resolution', 'unknown')
        bandwidth = best_stream.stream_info.bandwidth

        logger.info("Master playlist detected with %d streams", len(playlist.playlists))
        logger.info("Selected highest quality: %s, bandwidth: %d", resolution, bandwidth)

        # Construct absolute URL for the selected stream
        if best_stream.uri.startswith('http'):
            return best_stream.uri
        # Relative URL - construct absolute URL
        if base_url.endswith('/'):
            return base_url + best_stream.uri
        # Extract directory from base_url
        base_dir = '/'.join(base_url.split('/')[:-1])
        return base_dir + '/' + best_stream.uri

    def should_reload_master_playlist(self, _playlist):
        """
        Determine if master playlist should be reloaded.
        Override this method for type-specific logic.
        For base recorder (VOD), we never need to reload - endlist means complete.
        """
        return False

    def calculate_sleep_duration(self, target_duration):
        """
        Calculate how long to sleep between playlist fetches.
        Override this method for type-specific timing.
        """
        return min(target_duration / 2, 3.0) if target_duration else 1.0

    def record_stream(self, channel_uri, rec_dir, buffering):
        """
        The main recording loop - common for all HLS types.
        Specialized recorders can override specific parts via the hook methods.
        """
        logger.info("channel_uri: %s, rec_dir: %s", channel_uri, rec_dir)

        segment_index = 0
        section_index = -1
        empty_playlist_count = 0
        max_empty_playlists = 10
        failed_playlist_count = 0
        max_failed_playlists = 5
        reload_master_playlist = True
        media_playlist_url = None
        last_sequence = None
        failed_segment_count = 0
        segment_processor = None

        try:
            logger.info("Entering main recording loop, stop_event.is_set()=%s", self.stop_event.is_set())
            while not self.stop_event.is_set():
                if reload_master_playlist:
                    logger.info("Initializing session and segment processor")
                    # Use authenticated session from resolver (trusted architecture)
                    # channel_uri is the resolved media playlist URL
                    media_playlist_url = channel_uri

                    # Create/recreate segment processor with the media playlist base URL
                    if segment_processor is None:
                        logger.info("Creating HLSSegmentProcessor")
                        segment_processor = HLSSegmentProcessor(rec_dir, self.socketserver, media_playlist_url, "hls_basic")
                    reload_master_playlist = False
                    write_log(rec_dir, "none", section_index, segment_index, msg="media-playlist-ready")

                logger.info("Fetching playlist from: %s", media_playlist_url)

                playlist_text = get_playlist(self.session, media_playlist_url)
                if not playlist_text:
                    logger.error("Failed to fetch playlist (attempt %d/%d)", failed_playlist_count + 1, max_failed_playlists)
                    failed_playlist_count += 1
                    if failed_playlist_count >= max_failed_playlists:
                        logger.error("Too many failed playlist fetches. Reloading master playlist...")
                        reload_master_playlist = True
                        failed_playlist_count = 0  # Reset counter after reload
                        continue
                    time.sleep(1)
                    continue

                logger.info("Successfully fetched playlist, %d bytes", len(playlist_text))
                failed_playlist_count = 0

                playlist = self.process_playlist_content(playlist_text)

                # Check if this is a master playlist (has stream variants)
                if hasattr(playlist, 'playlists') and playlist.playlists:
                    logger.info("Detected master playlist, selecting best quality stream...")
                    selected_media_url = self.handle_master_playlist(playlist, media_playlist_url)
                    if selected_media_url:
                        media_playlist_url = selected_media_url
                        logger.info("Switched to media playlist: %s", media_playlist_url)
                        continue  # Fetch the actual media playlist
                    logger.error("Failed to select stream from master playlist")
                    reload_master_playlist = True
                    continue

                if self.should_reload_master_playlist(playlist):
                    reload_master_playlist = True
                    time.sleep(1)
                    continue

                target_duration = getattr(playlist, 'target_duration', 6) or 6

                if not playlist.segments:
                    empty_playlist_count += 1
                    logger.debug("No new segments found, waiting for next playlist update...")
                    if empty_playlist_count >= max_empty_playlists:
                        logger.info("Playlist has been empty for too long. Reloading master playlist...")
                        reload_master_playlist = True
                        continue
                    sleep_duration = self.calculate_sleep_duration(target_duration)
                    time.sleep(sleep_duration)
                    continue
                empty_playlist_count = 0

                logger.info("Segment list has %s segments, last_sequence=%s", len(playlist.segments), last_sequence)

                sequence_start = getattr(playlist, 'media_sequence', 0)
                logger.info("Processing segments: sequence_start=%s, total_segments=%s", sequence_start, len(playlist.segments))

                # Check if this is a VOD stream (has endlist) - reset last_sequence for VOD streams
                is_vod = hasattr(playlist, 'is_endlist') and playlist.is_endlist  # pylint: disable=no-member
                if is_vod and last_sequence is None:
                    logger.info("VOD stream detected, processing all segments from beginning")

                # Adjust buffering for VOD streams with few segments to ensure start message is sent
                effective_buffering = buffering
                if is_vod and len(playlist.segments) < buffering:
                    effective_buffering = len(playlist.segments)
                    logger.info("VOD stream has %d segments, adjusting buffering from %d to %d",
                                len(playlist.segments), buffering, effective_buffering)

                processed_segments = 0
                for idx, segment in enumerate(playlist.segments):
                    if self.stop_event.is_set():
                        logger.info("Recording stopped by user")
                        break

                    sequence = sequence_start + idx

                    # For VOD streams, process all segments on first pass
                    if not is_vod and last_sequence is not None and sequence <= last_sequence:
                        logger.debug("Skipping already processed segment %s (last_sequence=%s)", sequence, last_sequence)
                        continue  # Already processed

                    logger.info("Processing segment %s: %s", sequence, segment.uri)

                    segment = segment_processor.process_segment(self.session, target_duration, effective_buffering, segment)
                    if segment is None:
                        failed_segment_count += 1
                        if failed_segment_count >= 5:
                            logger.error("Too many failed segments, stopping recording...")
                            if self.socketserver:
                                self.socketserver.broadcast(["stop", {"reason": "error", "error_id": "failure", "channel": self.channel_uri, "rec_dir": rec_dir}])
                            self.stop_event.set()
                    else:
                        failed_segment_count = 0
                        processed_segments += 1
                    last_sequence = sequence

                logger.info("Finished processing %s segments this round", processed_segments)

                # Check if this is a VOD stream (has endlist) - if so, we're done after processing all segments
                if hasattr(playlist, 'is_endlist') and playlist.is_endlist:  # pylint: disable=no-member
                    logger.info("VOD stream complete (EXT-X-ENDLIST detected), all segments processed")
                    if self.socketserver:
                        self.socketserver.broadcast(["stop", {"reason": "complete", "channel": self.channel_uri, "rec_dir": rec_dir}])
                    self.stop_event.set()
                    break

                # Sleep before next playlist fetch
                sleep_duration = self.calculate_sleep_duration(target_duration)
                time.sleep(sleep_duration)

        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")
        except Exception as e:
            # Check for DRM protection error
            error_str = str(e)
            if error_str.startswith("DRM_PROTECTED:"):
                drm_info = error_str[14:]  # Remove "DRM_PROTECTED:" prefix
                logger.error("DRM protection detected: %s", drm_info)
                error_id = "drm_protected"
            else:
                logger.error("Recording error: %s", e)
                error_id = "failure"  # Default to failure, specific cases can override

            if self.socketserver:
                self.socketserver.broadcast(["stop", {"reason": "error", "error_id": error_id, "channel": self.channel_uri, "rec_dir": rec_dir}])
            traceback.print_exc()
        finally:
            if segment_processor and hasattr(segment_processor, 'ffmpeg_proc'):
                terminate_ffmpeg_process(segment_processor.ffmpeg_proc)
            logger.info("Recording stopped")

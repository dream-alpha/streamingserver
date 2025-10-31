# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Live Stream Recorder

This module handles live HLS streams that continuously update their playlists.
It monitors the playlist for new segments, downloads them, and creates a
continuous MPEG-TS file.

Live streams are identified by the absence of the #EXT-X-ENDLIST tag, indicating
that new segments will be added over time.
"""

from __future__ import annotations

import time
import traceback
import m3u8
from hls_playlist_utils import get_master_playlist, get_playlist
from drm_utils import detect_drm_in_content
from ffmpeg_utils import terminate_ffmpeg_process
from log_utils import write_log
from hls_segment_processor import HLSSegmentProcessor
from base_recorder import BaseRecorder
from debug import get_logger

logger = get_logger(__file__)


class HLS_Recorder_Live(BaseRecorder):
    """
    Manages the HLS recording lifecycle for live streams.

    This class handles live HLS streams by continuously monitoring the playlist
    for new segments, downloading and processing them in real-time. It maintains
    a persistent HTTP session, and handles playlist reloading and error recovery.

    Attributes:
        channel_uri (str): The URI of the HLS stream being recorded.
        socketserver (SocketServer): A reference to the command server.
        session (requests.Session): The session object for making HTTP requests.
    """

    def __init__(self):
        """Initializes the HLS_Recorder_Live instance."""
        super().__init__(self.__class__.__name__)
        self.session = None

    def record_start(self, resolve_result):
        """
        Starts the live recording process.

        This method sets up the recording environment, cleans up old files,
        and launches the `record_stream` method.

        Args:
            resolve_result (dict): Complete resolve result containing resolved_url,
                                 rec_dir, show_ads, buffering, auth_tokens,
                                 original_url, all_sources, etc.
        """
        super().record_start(resolve_result)  # Ensure parent cleanup
        # Extract data from resolve_result first
        channel_uri = resolve_result.get("resolved_url")
        rec_dir = resolve_result.get("rec_dir", "/tmp")
        buffering = resolve_result.get("buffering", 5)
        self.session = resolve_result.get("session")  # Use consistent key name

        if not self.session:
            raise ValueError("No session available for live HLS recording")

        logger.info("#" * 70)
        logger.info("Starting live HLS recording for channel: %s", channel_uri)
        logger.info("#" * 70)

        # Start the recording - BaseRecorder handles the threading
        self.record_stream(channel_uri, rec_dir, buffering)

    def record_stream(self, channel_uri, rec_dir, buffering):
        """
        The main recording loop for a live HLS stream.

        Continuously monitors the playlist for new segments and processes them.
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
            while not self.stop_event.is_set():
                if reload_master_playlist:
                    # Use authenticated session from resolver (trusted architecture)
                    media_playlist_url = get_master_playlist(self.session, channel_uri)

                    # Create/recreate segment processor with the media playlist URL
                    if segment_processor is None:
                        logger.info("Creating HLSSegmentProcessor")
                        segment_processor = HLSSegmentProcessor(rec_dir, self.socketserver, media_playlist_url, "hls_live")

                    reload_master_playlist = False
                    write_log(rec_dir, "none", section_index, segment_index, msg="load-master-playlist")

                playlist_text = get_playlist(self.session, media_playlist_url)
                if not playlist_text:
                    logger.error("Failed to fetch playlist, retrying...")
                    failed_playlist_count += 1
                    if failed_playlist_count >= max_failed_playlists:
                        logger.error("Too many failed playlist fetches. Reloading master playlist...")
                        reload_master_playlist = True
                        continue
                    time.sleep(1)
                    continue
                failed_playlist_count = 0

                playlist = m3u8.loads(playlist_text)

                # Check for DRM protection in playlist
                try:
                    if detect_drm_in_content(playlist_text, content_type="m3u8")["has_drm"]:
                        raise ValueError("DRM_PROTECTED: Stream uses DRM protection (detected in playlist)")
                except ValueError as e:
                    # Check for DRM protection error
                    error_str = str(e)
                    if error_str.startswith("DRM_PROTECTED:"):
                        drm_info = error_str[14:]  # Remove "DRM_PROTECTED:" prefix
                        logger.error("DRM protection detected: %s", drm_info)
                        super().on_thread_error(Exception(f"DRM Protected Stream: {drm_info}"), error_id="drm_protected", recorder_id="hls_live")
                        self.stop_event.set()
                        break
                    # Re-raise non-DRM ValueError
                    raise

                if playlist.is_endlist:  # pylint: disable=no-member
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
                    time.sleep(1)
                    continue
                empty_playlist_count = 0

                logger.debug("Segment list has %s new segments", len(playlist.segments))

                sequence_start = playlist.media_sequence  # pylint: disable=no-member
                for idx, segment in enumerate(playlist.segments):
                    if self.stop_event.is_set():
                        logger.info("Recording stopped by user")
                        break

                    sequence = sequence_start + idx
                    if last_sequence is not None and sequence <= last_sequence:
                        continue  # Already processed

                    try:
                        segment = segment_processor.process_segment(self.session, target_duration, buffering, segment)
                        if segment is None:
                            failed_segment_count += 1
                            if failed_segment_count >= 5:
                                super().on_thread_error(Exception("Too many failed segments"), error_id="failure", recorder_id="hls_live")
                                self.stop_event.set()
                                break  # Exit the for loop, will exit while loop on next iteration

                        failed_segment_count = 0
                    except ValueError as e:
                        # Check for DRM protection error
                        error_str = str(e)
                        if error_str.startswith("DRM_PROTECTED:"):
                            drm_info = error_str[14:]  # Remove "DRM_PROTECTED:" prefix
                            logger.error("DRM protection detected: %s", drm_info)
                            super().on_thread_error(Exception(f"DRM Protected Stream: {drm_info}"), error_id="drm_protected", recorder_id="hls_live")
                            self.stop_event.set()
                            break

                        # Re-raise non-DRM ValueError
                        raise
                    last_sequence = sequence

        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")
        except Exception as e:
            # Check for DRM protection error
            error_str = str(e)
            if error_str.startswith("DRM_PROTECTED:"):
                drm_info = error_str[14:]  # Remove "DRM_PROTECTED:" prefix
                logger.error("DRM protection detected: %s", drm_info)
                super().on_thread_error(Exception(f"DRM Protected Stream: {drm_info}"), error_id="drm_protected", recorder_id="hls_live")
            else:
                logger.error("Recording error: %s", e)
                super().on_thread_error(e, error_id="failure", recorder_id="hls_live")
            traceback.print_exc()
            raise
        finally:
            if segment_processor and hasattr(segment_processor, 'ffmpeg_proc'):
                terminate_ffmpeg_process(segment_processor.ffmpeg_proc)
            logger.info("Recording stopped")

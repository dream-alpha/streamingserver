# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Base HLS Recorder Class

This module provides the base functionality that is common to all HLS recorder types.
Specialized recorders inherit from this class and override specific methods to handle
their unique requirements.
"""

from __future__ import annotations

import time
import threading
import traceback
import glob
import subprocess
from typing import TYPE_CHECKING
import m3u8
from hls_playlist_utils import get_playlist
from ffmpeg_utils import terminate_ffmpeg_process
from log_utils import write_log
from session_utils import get_session
from hls_segment_processor import HLSSegmentProcessor
from debug import get_logger

if TYPE_CHECKING:
    from socket_server import SocketServer

logger = get_logger(__file__)


class HLS_Recorder_Base:
    """
    Base class for all HLS recorders.

    This class provides common functionality like session management,
    playlist fetching, and basic recording lifecycle. Specialized
    recorders inherit from this and override specific methods.
    """

    def __init__(self):
        """Initializes the base HLS_Recorder instance."""
        self.is_running = False
        self.stop_event = threading.Event()
        self.channel_uri = ""
        self.socketserver = None
        self.session = None

    def prepare_recording(self, channel_uri, rec_dir, show_ads):  # pylint: disable=unused-argument
        """
        Prepare the recording environment.
        Override this method in specialized recorders for type-specific preparation.
        """
        logger.info("Preparing recording for channel: %s", channel_uri)
        self.channel_uri = channel_uri

        # Clean up old files
        pattern = rec_dir + "/stream*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
        logger.debug("Removed old files: %s", pattern)

    def process_playlist_content(self, playlist_text):
        """
        Process playlist content.
        Override this method in specialized recorders for type-specific processing.
        """
        return m3u8.loads(playlist_text)

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
        self.is_running = True

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
            logger.info("DEBUG: Entering main recording loop, stop_event.is_set()=%s", self.stop_event.is_set())
            while not self.stop_event.is_set():
                if reload_master_playlist:
                    logger.info("DEBUG: Initializing session and segment processor")
                    self.session = get_session()
                    # channel_uri is already the media playlist URL (resolved by hls_switch)
                    media_playlist_url = channel_uri

                    # Create/recreate segment processor with the media playlist base URL
                    if segment_processor is None:
                        logger.info("DEBUG: Creating HLSSegmentProcessor")
                        segment_processor = HLSSegmentProcessor(rec_dir, self.socketserver, media_playlist_url)
                    reload_master_playlist = False
                    write_log(rec_dir, "none", section_index, segment_index, msg="media-playlist-ready")

                logger.info("DEBUG: Fetching playlist from: %s", media_playlist_url)

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

                logger.info("DEBUG: Successfully fetched playlist, %d bytes", len(playlist_text))
                failed_playlist_count = 0

                playlist = self.process_playlist_content(playlist_text)

                if self.should_reload_master_playlist(playlist):
                    reload_master_playlist = True
                    time.sleep(1)
                    continue

                target_duration = getattr(playlist, 'target_duration', 6)

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

                logger.debug("Segment list has %s new segments", len(playlist.segments))

                sequence_start = getattr(playlist, 'media_sequence', 0)
                for idx, segment in enumerate(playlist.segments):
                    if self.stop_event.is_set():
                        logger.info("Recording stopped by user")
                        break

                    sequence = sequence_start + idx
                    if last_sequence is not None and sequence <= last_sequence:
                        continue  # Already processed

                    segment = segment_processor.process_segment(self.session, target_duration, buffering, segment)
                    if segment is None:
                        failed_segment_count += 1
                        if failed_segment_count >= 5:
                            logger.error("Too many failed segments, stopping recording...")
                            if self.socketserver:
                                self.socketserver.broadcast(["stop", {"reason": "error", "channel": self.channel_uri, "rec_dir": rec_dir}])
                            self.stop_event.set()
                    else:
                        failed_segment_count = 0
                    last_sequence = sequence

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
            logger.error("Recording error: %s", e)
            if self.socketserver:
                self.socketserver.broadcast(["stop", {"reason": "error", "channel": self.channel_uri, "rec_dir": rec_dir}])
            traceback.print_exc()
        finally:
            terminate_ffmpeg_process(segment_processor.ffmpeg_proc)
            self.is_running = False
            logger.info("Recording stopped")

    def start(self, channel_uri, rec_dir, show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        Starts the recording process in a new thread.

        Args:
            channel_uri (str): HLS playlist URL
            rec_dir (str): Output directory
            show_ads (bool): Whether to show ads
            buffering (int): Number of segments to buffer
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("#" * 70)
        logger.info("Starting HLS recording for channel: %s", channel_uri)
        logger.info("#" * 70)

        # Store authentication tokens and metadata
        self.auth_tokens = auth_tokens
        self.original_page_url = original_page_url
        self.all_sources = all_sources

        self.stop()

        self.prepare_recording(channel_uri, rec_dir, show_ads)

        while self.is_running:
            logger.info("Recording is still running. waiting...")
            time.sleep(0.5)

        self.stop_event.clear()
        threading.Thread(target=self.record_stream, args=(channel_uri, rec_dir, buffering), daemon=True).start()

    def stop(self):
        """Signals the recording thread to stop."""
        logger.info("Stopping recording...")
        self.stop_event.set()

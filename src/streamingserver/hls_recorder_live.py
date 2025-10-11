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

import re
import time
import threading
import traceback
import glob
import subprocess
from typing import TYPE_CHECKING
import m3u8
from hls_playlist_utils import get_master_playlist, get_playlist
from ffmpeg_utils import terminate_ffmpeg_process
from log_utils import write_log
from session_utils import get_session
from hls_segment_processor import HLSSegmentProcessor
from debug import get_logger

if TYPE_CHECKING:
    from socket_server import SocketServer

logger = get_logger(__file__)


class HLS_Recorder_Live:
    socketserver: SocketServer | None

    """
    Manages the HLS recording lifecycle for live streams.

    This class handles live HLS streams by continuously monitoring the playlist
    for new segments, downloading and processing them in real-time. It maintains
    the recording state, manages a persistent HTTP session, and handles playlist
    reloading and error recovery.

    Attributes:
        is_running (bool): True if a recording is currently active.
        stop_event (threading.Event): Event used to signal the recording loop to stop.
        channel_uri (str): The URI of the HLS stream being recorded.
        socketserver (SocketServer): A reference to the command server.
        session (requests.Session): The session object for making HTTP requests.
    """

    def __init__(self):
        """Initializes the HLS_Recorder_Live instance."""
        self.is_running = False
        self.stop_event = threading.Event()
        self.channel_uri = ""
        self.socketserver = None
        self.session = None

    def record_stream(self, channel_uri, rec_dir, buffering):
        """
        The main recording loop for a live HLS stream.

        Continuously monitors the playlist for new segments and processes them.
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

        segment_processor = HLSSegmentProcessor(rec_dir, self.socketserver)

        try:
            while not self.stop_event.is_set():
                if reload_master_playlist:
                    self.session = get_session()
                    media_playlist_url = get_master_playlist(self.session, channel_uri)
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

                if playlist.is_endlist:  # pylint: disable=no-member
                    reload_master_playlist = True
                    time.sleep(1)
                    continue

                target_duration = playlist.target_duration  # pylint: disable=no-member

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

                    segment = segment_processor.process_segment(self.session, target_duration, buffering, segment)
                    if segment is None:
                        failed_segment_count += 1
                        if failed_segment_count >= 5:
                            logger.error("Too many failed segments, stopping recording...")
                            self.socketserver.broadcast(["stop", {"reason": "error", "channel": self.channel_uri, "rec_dir": rec_dir}])
                            self.stop_event.set()
                    else:
                        failed_segment_count = 0
                    last_sequence = sequence

        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")
        except Exception as e:
            logger.error("Recording error: %s", e)
            self.socketserver.broadcast(["stop", {"reason": "error", "channel": self.channel_uri, "rec_dir": rec_dir}])
            traceback.print_exc()
        finally:
            terminate_ffmpeg_process(segment_processor.ffmpeg_proc)
            self.is_running = False
            logger.info("Recording stopped")

    def start(self, channel_uri, rec_dir, show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        Starts the live recording process in a new thread.

        This method sets up the recording environment, cleans up old files,
        and launches the `record_stream` method in a background thread to
        avoid blocking.

        Args:
            channel_uri (str): The channel ID or full URL to record.
            rec_dir (str): The directory of the output recording file.
            show_ads (bool): Show ads (true) or fillers (false)
            buffering (int): Number of segments to be buffered
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("#" * 70)
        logger.info("Starting live HLS recording for channel: %s", channel_uri)
        logger.info("#" * 70)

        # Store authentication tokens and metadata
        self.auth_tokens = auth_tokens
        self.original_page_url = original_page_url
        self.all_sources = all_sources

        self.stop()

        if not show_ads:
            channel_id = ""
            if channel_uri.startswith("http"):
                # Extract channel_id from channel_uri using regex
                match = re.search(r"/channel/([^/]+)/", channel_uri)
                if match:
                    channel_id = match.group(1)
                    logger.debug("Extracted channel_id: %s", channel_id)
            else:
                channel_id = channel_uri
            if channel_id:
                channel_uri = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{channel_id}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="
        self.channel_uri = channel_uri
        logger.debug("Using channel URI: %s", self.channel_uri)

        pattern = rec_dir + "/stream*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
        logger.debug("Removed old files: %s", pattern)

        while self.is_running:
            logger.info("Recording is still running. waiting...")
            time.sleep(0.5)

        self.stop_event.clear()
        threading.Thread(target=self.record_stream, args=(self.channel_uri, rec_dir, buffering), daemon=True).start()

    def stop(self):
        """Signals the recording thread to stop."""
        logger.info("Stopping recording...")
        self.stop_event.set()

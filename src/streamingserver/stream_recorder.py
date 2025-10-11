# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Stream Recorder
"""

from __future__ import annotations
from typing import TYPE_CHECKING
from mp4_recorder import MP4_Recorder
from hls_switch import HLS_Switch
from stream_utils import detect_stream_type
from debug import get_logger

if TYPE_CHECKING:
    from socket_server import SocketServer

logger = get_logger(__file__)


class StreamRecorder:

    def __init__(self):
        """Initializes the StreamRecorder instance."""
        self.server: SocketServer | None = None
        self.mp4_recorder: MP4_Recorder | None = None
        self.hls_switch: HLS_Switch | None = None

    def record_stream(self, channel_uri, rec_dir, show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        The main stream recording selection.
        Expects channel_uri to be an already resolved streaming URL.

        Args:
            channel_uri (str): Resolved streaming URL
            rec_dir (str): Recording directory
            show_ads (bool): Whether to show ads
            buffering (int): Buffering settings
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("Recording stream - URI: %s, Directory: %s", channel_uri, rec_dir)

        if auth_tokens:
            logger.info("Authentication tokens provided for protected stream")
        if original_page_url:
            logger.info("Original page URL: %s", original_page_url)
        if all_sources:
            logger.info("All sources available: %d alternatives", len(all_sources))

        stream_type = detect_stream_type(channel_uri)
        match stream_type:
            case "HLS" | "HLS_M4S":
                # Route all HLS streams (standard and M4S) through HLS_Switch
                # The switch will analyze and route to the appropriate specialized recorder
                logger.info("Detected %s stream, routing through HLS_Switch", stream_type)
                self.hls_switch = HLS_Switch(self.server)
                logger.info("DEBUG: Creating HLS_Switch with socketserver: %s", self.server)

                self.hls_switch.hls_switch(
                    channel_uri,
                    rec_dir,
                    show_ads,
                    buffering,
                    auth_tokens,
                    original_page_url,
                    all_sources,
                )
            case "DASH":
                logger.info("Detected DASH stream: %s", channel_uri)
                # TODO: Implement DASH recorder
            case "MP4":
                logger.info("Detected MP4 stream: %s", channel_uri)
                self.mp4_recorder = MP4_Recorder()
                self.mp4_recorder.socketserver = self.server

                self.mp4_recorder.start(channel_uri, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
            case "WebM":
                logger.info("Detected WebM stream: %s", channel_uri)
                # TODO: Implement WebM recorder
            case "TS":
                logger.info("Detected TS stream: %s", channel_uri)
                # TODO: Implement TS recorder
            case "HTML webpage":
                logger.error("Received HTML webpage URL instead of video stream: %s", channel_uri)
                logger.error("This indicates the URL was not properly resolved upstream")
                logger.error("Please ensure URL resolution happens before calling stream_recorder")
            case _:
                logger.warning("Unknown or unsupported stream type: %s (URL: %s)", stream_type, channel_uri)

    def stop(self):
        """Stops the active recorder."""
        logger.info("Stopping stream recorder...")
        if self.mp4_recorder is not None:
            self.mp4_recorder.stop()
            self.mp4_recorder = None
        if self.hls_switch is not None:
            self.hls_switch.stop()
            self.hls_switch = None

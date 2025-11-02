#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Recorder Manager
Just start/stop different recorder types with thread safety
"""
from __future__ import annotations

# Import recorder classes
from base_recorder import BaseRecorder
from mp4_recorder import MP4_Recorder
from hls_recorder_basic import HLS_Recorder_Basic
from hls_recorder_live import HLS_Recorder_Live
from hls_recorder_m4s import HLS_Recorder_M4S

# Setup logging
from debug import get_logger
logger = get_logger(__file__)


class Recorder:
    """Manages single active recorder - ensures only one runs at a time"""

    def __init__(self):
        self.current_recorder: BaseRecorder | None = None
        self.recorder_ids = {
            'mp4': MP4_Recorder,
            'hls_basic': HLS_Recorder_Basic,
            'hls_live': HLS_Recorder_Live,
            'hls_m4s': HLS_Recorder_M4S
        }

    def start_recorder(self, recorder_id: str, resolve_result: dict) -> bool:
        """Start recorder by type name - stops current recorder and waits"""
        if recorder_id not in self.recorder_ids:
            logger.error(f"Unknown recorder type: {recorder_id}")
            return False

        # Stop current recorder and wait for it to fully stop
        if self.current_recorder and self.current_recorder.is_running:
            logger.info(f"Stopping current {self.current_recorder.name} before starting {recorder_id}...")
            if not self.stop():
                logger.error("Failed to stop current recorder")
                return False
            logger.info("Current recorder fully stopped, starting new one...")

        recorder_class = self.recorder_ids[recorder_id]
        self.current_recorder = recorder_class()
        return self.current_recorder.start_thread(resolve_result)

    def stop(self) -> bool:
        """Stop current recorder and wait until it's fully stopped"""
        if self.current_recorder and self.current_recorder.is_running:
            logger.info(f"Stopping {self.current_recorder.name} and waiting for completion...")
            success = self.current_recorder.stop()  # This already waits with thread.join()
            if success:
                logger.info(f"{self.current_recorder.name} has fully stopped")
            return success
        return True

    def status(self) -> str:
        """Get current status"""
        if self.current_recorder and self.current_recorder.is_running:
            return f"Running: {self.current_recorder.name}"
        return "No recorder running"

    def get_available_types(self) -> list:
        """Get list of available recorder types"""
        return list(self.recorder_ids.keys())

    def record_stream(self, resolve_result):
        """
        The main stream recording selection.
        Expects resolve_result to contain all necessary data including resolved URL,
        auth data, recording directory, and settings.

        Args:
            resolve_result (dict): Complete resolver result with resolved_url, auth_tokens,
                                 rec_dir, show_ads, buffering, etc.
        """
        # Extract data from resolve_result
        channel_uri = resolve_result.get("resolved_url")
        auth_tokens = resolve_result.get("auth_tokens")
        original_page_url = resolve_result.get("original_url")
        recorder_id = resolve_result.get("recorder_id")
        rec_dir = resolve_result.get("rec_dir", "/tmp")
        # show_ads and buffering passed via resolve_result to sub-recorders

        logger.info("Recording stream - URI: %s, Directory: %s", channel_uri, rec_dir)
        if auth_tokens:
            logger.info("Authentication tokens provided for protected stream")
        if original_page_url:
            logger.info("Original page URL: %s", original_page_url)

        # Use recorder_id for direct recorder selection
        if not recorder_id:
            logger.error("No recorder_id specified in resolve_result - this is required!")
            logger.error("All resolvers must specify a recorder_id (mp4, hls_basic, hls_live, hls_m4s)")
            return

        logger.info("Using recorder: %s (specified by resolver)", recorder_id)
        self.start_recorder(recorder_id, resolve_result)

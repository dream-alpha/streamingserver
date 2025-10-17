# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
MP4 Recorder

This module handles direct MP4 video downloads.
"""

from __future__ import annotations

import os
import time
import threading
import requests
from base_recorder import BaseRecorder
from string_utils import format_size
from auth_utils import apply_auth_tokens_to_session
from debug import get_logger


logger = get_logger(__file__)


class MP4_Recorder(BaseRecorder):
    """Direct MP4 recorder for streaming server"""

    def __init__(self):
        """Initialize the MP4 recorder"""
        super().__init__(self.__class__.__name__)  # Pass name to parent class
        # Recording state - no thread management needed
        self.progress = 0
        self.total_size = 0
        self.downloaded_size = 0
        self.playback_timer = None

    def record_start(self, resolve_result):
        """
        Setup MP4 recording parameters (called from record_start)

        Args:
            resolve_result (dict): Complete resolve result containing resolved_url,
                                 rec_dir, show_ads, buffering, auth_tokens,
                                 original_url, all_sources, socketserver, etc.
        """
        super().record_start(resolve_result)  # Ensure parent cleanup
        # Extract and setup recording parameters
        self.video_url = resolve_result.get("resolved_url")
        self.auth_tokens = resolve_result.get("auth_tokens")
        self.original_page_url = resolve_result.get("original_url")
        self.all_sources = resolve_result.get("all_sources")
        self.session = resolve_result.get("session")  # Use consistent key name
        self.rec_dir = resolve_result.get("rec_dir", "/tmp")

        # Ensure session has the required auth tokens applied cleanly
        if self.session and self.auth_tokens:
            # Use centralized session auth token application with cookie deduplication
            apply_auth_tokens_to_session(self.session, self.auth_tokens)
        elif not self.session and self.auth_tokens:
            # Create new session with auth tokens if none provided
            self.session = requests.Session()
            apply_auth_tokens_to_session(self.session, self.auth_tokens)

        # Start the actual recording
        self.record_stream(self.video_url, self.rec_dir)

    def record_stream(self, video_url, rec_dir):
        """
        Main recording function for MP4 files - pure recording logic

        Args:
            video_url (str): Direct MP4 URL
            rec_dir (str): Output directory
        """
        logger.info("MP4 recording started: %s -> %s", video_url, rec_dir)

        try:
            output_file = os.path.join(rec_dir, "stream_0.mp4")

            self.playback_timer = threading.Timer(3.0, self.start_playback, args=(video_url, output_file))
            self.playback_timer.daemon = True
            self.playback_timer.start()

            self._direct_download(video_url, output_file)

        except Exception as e:
            logger.error("MP4 recording error: %s", e)
            raise  # Re-raise so BaseRecorder thread wrapper can handle it

        if self.playback_timer and self.playback_timer.is_alive():
            logger.info("Cancelling playback timer and sending start playback immediately")
            self.playback_timer.cancel()
            self.playback_timer = None
            self.start_playback(video_url, output_file)

        logger.info("MP4 recording completed successfully")

    def on_thread_ended(self):
        """Called when recording thread ends - cleanup timer"""
        super().on_thread_ended()  # Call parent cleanup
        if self.playback_timer and self.playback_timer.is_alive():
            logger.info("Cancelling playback timer")
            self.playback_timer.cancel()
            self.playback_timer = None

    def _direct_download(self, url, output_file):
        """
        Download MP4 directly using requests with progress reporting

        Args:
            url (str): MP4 file URL
            output_file (str): Output file path

        """
        try:
            session = self.session
            if not session:
                raise ValueError("No session available for downloading")

            # Log session headers for debugging
            logger.info("Session headers for download: %s", dict(session.headers))
            logger.info("Session cookies for download: %s", dict(session.cookies))

            # Additional cookie debugging
            if hasattr(session.cookies, '_cookies'):
                logger.info("Session cookie jar details: %s", session.cookies._cookies)

            # Check for duplicate cookie names
            cookie_names = list(session.cookies.keys())
            duplicate_names = [name for name in set(cookie_names) if cookie_names.count(name) > 1]
            if duplicate_names:
                logger.warning("Detected duplicate cookie names: %s", duplicate_names)

            # Check if we have the critical xHamster headers
            has_referer = any(k.lower() == 'referer' for k in session.headers.keys())
            has_origin = any(k.lower() == 'origin' for k in session.headers.keys())
            logger.info("Critical headers present - Referer: %s, Origin: %s", has_referer, has_origin)

            if not has_referer or not has_origin:
                logger.warning("Missing critical headers!")
                # Try to add them from auth_tokens if available
                if self.auth_tokens and self.auth_tokens.get("headers"):
                    auth_headers = self.auth_tokens.get("headers", {})
                    critical_headers = {'referer', 'origin'}
                    for key, value in auth_headers.items():
                        if key.lower() in critical_headers:
                            logger.info("Adding missing header from auth_tokens: %s = %s", key, value)
                            session.headers[key] = value
            try:
                head_response = session.head(url, timeout=10)
                self.total_size = int(head_response.headers.get('Content-Length', 0))
            except Exception as e:
                logger.warning("HEAD request failed: %s. Proceeding without file size.", e)
                self.total_size = 0

            if self.total_size <= 0:
                logger.warning("Couldn't determine file size, progress reporting will be unavailable")
                self.total_size = 0  # Reset to avoid division by zero

            logger.info("Starting direct MP4 download%s",
                        f" of {self.total_size} bytes" if self.total_size > 0 else "")

            # Stream the download with progress reporting
            with session.get(url, stream=True, timeout=60) as response:
                if response.status_code == 403:
                    logger.error("HTTP 403 Forbidden error when accessing video URL")
                    logger.error("This usually indicates missing or incorrect headers (especially Referer)")
                    logger.error("URL: %s", url[:100] + "..." if len(url) > 100 else url)
                    logger.error("Session headers: %s", dict(session.headers))
                    raise PermissionError(f"403 Forbidden - CDN access denied. URL: {url[:80]}...")
                response.raise_for_status()

                # Initialize variables for progress tracking
                self.downloaded_size = 0
                last_report_time = time.time()
                last_report_size = 0

                with open(output_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk and not self.stop_event.is_set():
                            f.write(chunk)
                            self.downloaded_size += len(chunk)

                            # Update progress and report at reasonable intervals (every ~2%)
                            if self.total_size > 0:
                                current_time = time.time()
                                progress = int((self.downloaded_size / self.total_size) * 100)

                                # Report progress every 2% or 3 seconds, whichever comes first
                                if (progress - self.progress >= 2
                                        or current_time - last_report_time >= 3):
                                    self.progress = progress

                                    # Calculate download speed
                                    elapsed = current_time - last_report_time
                                    bytes_since_last = self.downloaded_size - last_report_size
                                    speed = bytes_since_last / elapsed if elapsed > 0 else 0

                                    # Update for next iteration
                                    last_report_time = current_time
                                    last_report_size = self.downloaded_size

                                    logger.info("Download progress: %d%% (%s/%s, %.2f MB/s)",
                                                progress,
                                                format_size(self.downloaded_size),
                                                format_size(self.total_size),
                                                speed / 1024 / 1024)

            # Verify the download was successful
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                if self.total_size > 0 and abs(os.path.getsize(output_file) - self.total_size) > 100000:
                    logger.warning("Download size mismatch: %d vs expected %d",
                                   os.path.getsize(output_file), self.total_size)
                    # Continue anyway, the file might still be usable

                # Log final 100% completion
                final_size = os.path.getsize(output_file)
                logger.info("Download progress: 100%% (%s/%s)",
                            format_size(final_size),
                            format_size(self.total_size) if self.total_size > 0 else format_size(final_size))
                logger.info("MP4 download completed successfully")
            else:
                logger.error("Downloaded file is empty or missing")
                super().on_thread_error(Exception("Downloaded file is empty or missing"))

        except Exception as e:
            logger.error("Error in direct MP4 download: %s", e)
            raise

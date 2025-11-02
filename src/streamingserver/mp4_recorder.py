# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
MP4 Recorder

This module handles direct MP4 video downloads.
"""

from __future__ import annotations

import os
import time
import requests
from base_recorder import BaseRecorder
from string_utils import format_size
from debug import get_logger


logger = get_logger(__file__)


class MP4_Recorder(BaseRecorder):
    """Direct MP4 recorder for streaming server"""

    def __init__(self):
        """Initialize the MP4 recorder"""
        super().__init__(self.__class__.__name__)
        # Recording state - no thread management needed
        self.progress = 0
        self.total_size = 0
        self.downloaded_size = 0
        self.recording_has_started = False

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
        self.resolved_url = resolve_result.get("resolved_url")
        self.auth_tokens = resolve_result.get("auth_tokens")
        self.session = resolve_result.get("session")  # Use pre-authenticated session from resolver
        self.rec_dir = resolve_result.get("rec_dir", "/tmp")

        # Use the pre-authenticated session from resolver, or create a basic session as fallback
        if not self.session:
            logger.warning("No authenticated session provided by resolver - creating basic session")
            self.session = requests.Session()

        # Start the actual recording
        self.record_stream(self.resolved_url, self.rec_dir)

    def record_stream(self, url, rec_dir):
        """
        Main recording function for MP4 files - pure recording logic

        Args:
            url (str): Direct MP4 URL
            rec_dir (str): Output directory
        """
        logger.info("MP4 recording started: %s -> %s", url, rec_dir)

        try:
            output_file = os.path.join(rec_dir, "stream_0.mp4")

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

            try:
                head_response = session.head(url, timeout=10)
                self.total_size = int(head_response.headers.get('Content-Length', 0))
            except Exception as e:
                logger.warning("HEAD request failed: %s. Proceeding without file size.", e)
                self.total_size = 0

            logger.info("Starting direct MP4 download%s",
                        f" of {self.total_size} bytes" if self.total_size > 0 else "")

            # Check if stop was requested before starting download
            if self.stop_event.is_set():
                logger.info("Stop requested before download could begin")
                return

            # Stream the download with progress reporting
            logger.info("Initiating HTTP GET request...")
            with session.get(url, stream=True, timeout=60) as response:
                logger.info("HTTP response received, status: %d", response.status_code)
                if response.status_code == 403:
                    logger.error("HTTP 403 Forbidden error when accessing video URL")
                    logger.error("This usually indicates missing or incorrect headers (especially Referer)")
                    logger.error("URL: %s", url)
                    logger.error("Session headers: %s", dict(session.headers))
                    raise PermissionError(f"403 Forbidden - CDN access denied. URL: {url}...")
                response.raise_for_status()

                # Initialize variables for progress tracking
                self.downloaded_size = 0
                last_report_time = time.time()
                last_report_size = 0
                chunk_count = 0

                logger.info("Opening output file: %s", output_file)
                with open(output_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        # Check stop event first
                        if self.stop_event.is_set():
                            logger.info("Stop event detected during download after %d chunks (%s downloaded)",
                                        chunk_count, format_size(self.downloaded_size))
                            break

                        if chunk:
                            f.write(chunk)
                            self.downloaded_size += len(chunk)
                            chunk_count += 1

                            # Log first chunk to confirm download started
                            if chunk_count == 1:
                                logger.info("First chunk received and written (%d bytes)", len(chunk))
                            elif chunk_count == 400 and not self.recording_has_started:
                                self.recording_has_started = True
                                self.start_playback(url, output_file, "mp4")

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
                    logger.warning("Download size mismatch: %d versus expected %d",
                                   os.path.getsize(output_file), self.total_size)
                    # Continue anyway, the file might still be usable

                # Log final 100% completion
                final_size = os.path.getsize(output_file)
                logger.info("Download progress: 100%% (%s/%s)",
                            format_size(final_size),
                            format_size(self.total_size) if self.total_size > 0 else format_size(final_size))
                if not self.recording_has_started:
                    self.recording_has_started = True
                    self.start_playback(url, output_file, "mp4")
                logger.info("MP4 download completed successfully")
            else:
                logger.error("Downloaded file is empty or missing")
                self.on_thread_error(Exception("Downloaded file is empty or missing"), recorder_id="mp4")

        except Exception as e:
            logger.error("Error in direct MP4 download: %s", e)
            self.on_thread_error(e, recorder_id="mp4")

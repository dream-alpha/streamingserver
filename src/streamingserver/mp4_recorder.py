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
import subprocess
import glob
import requests
from debug import get_logger


logger = get_logger(__file__)


class MP4_Recorder:
    """Direct MP4 recorder for streaming server"""

    def __init__(self):
        """Initialize the MP4 recorder"""
        self.is_running = False
        self.stop_event = threading.Event()
        self.video_url = ""
        self.socketserver = None
        self.download_thread = None
        self.progress = 0
        self.total_size = 0
        self.downloaded_size = 0
        self.output_file = ""

    def _start_playback(self):
        """
        Function called by timer after 3 seconds to start playback
        This can be used to signal that recording has started and playback can begin
        """
        logger.info("Playback started after 3-second delay")

        # Notify client that playback can start
        if self.socketserver:
            self.socketserver.broadcast(["start", {"url": self.video_url, "rec_file": self.output_file, "section_index": 0, "segment_index": 0}])

    def start(self, video_url, rec_dir, _show_ads=False, _buffering=0, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        Start MP4 recording

        Args:
            video_url (str): Direct MP4 URL
            rec_dir (str): Output directory
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("Starting MP4 recording for: %s", video_url)

        # Store authentication tokens and metadata
        self.auth_tokens = auth_tokens
        self.original_page_url = original_page_url
        self.all_sources = all_sources

        self.stop()

        self.video_url = video_url

        # Clean up old files
        pattern = rec_dir + "/stream*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)

        while self.is_running:
            logger.info("Recording is still running. waiting...")
            time.sleep(0.5)

        self.stop_event.clear()
        self.download_thread = threading.Thread(
            target=self.record_stream,
            args=(video_url, rec_dir),
            daemon=True
        )
        self.download_thread.start()

    def record_stream(self, video_url, rec_dir):
        """
        Main recording function for MP4 files

        Args:
            video_url (str): Direct MP4 URL
            rec_dir (str): Output directory
        """
        logger.info("MP4 recording started: %s -> %s", video_url, rec_dir)
        self.is_running = True

        # Start a 3-second timer to invoke start_playback
        playback_timer = threading.Timer(3.0, self._start_playback)
        playback_timer.daemon = True
        playback_timer.start()

        try:
            # Set up output file
            output_file = self.output_file = os.path.join(rec_dir, "stream_0.mp4")
            self._direct_download(video_url, output_file)

        except Exception as e:
            logger.error("MP4 recording error: %s", e)
            if self.socketserver:
                self.socketserver.broadcast([
                    "stop", {
                        "reason": "error",
                        "message": str(e),
                        "url": video_url,
                        "rec_dir": rec_dir
                    }
                ])
        finally:
            self.is_running = False
            logger.info("MP4 recording stopped")

    def _direct_download(self, url, output_file):
        """
        Download MP4 directly using requests with progress reporting

        Args:
            url (str): MP4 file URL
            output_file (str): Output file path

        Returns:
            bool: True if download succeeded, False otherwise
        """
        try:
            # Default headers
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Range": "bytes=0-",  # Support resumable downloads
                "Referer": getattr(self, 'original_page_url', ''),
            }

            # Clean up headers (remove empty values)
            headers = {k: v for k, v in headers.items() if v}

            # Get file size first for progress reporting
            session = requests.Session()

            try:
                head_response = session.head(url, headers=headers, timeout=10)
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
            with session.get(url, headers=headers, stream=True, timeout=60) as response:
                response.raise_for_status()

                # Initialize variables for progress tracking
                self.downloaded_size = 0
                last_report_time = time.time()
                last_report_size = 0

                with open(output_file, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if self.stop_event.is_set():
                            logger.info("Download stopped by user")
                            return False

                        if chunk:
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
                                                self._format_size(self.downloaded_size),
                                                self._format_size(self.total_size),
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
                            self._format_size(final_size),
                            self._format_size(self.total_size) if self.total_size > 0 else self._format_size(final_size))
                logger.info("MP4 download completed successfully")
                return True

            logger.error("Downloaded file is empty or missing")
            return False

        except requests.RequestException as e:
            logger.error("Direct MP4 download failed: %s", e)
            return False
        except Exception as e:
            logger.error("Unexpected error in direct MP4 download: %s", e)
            return False

    def _format_size(self, size_bytes):
        """Format bytes as human-readable size"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def stop(self):
        """Stop the recording"""
        logger.info("Stopping MP4 recording...")
        self.stop_event.set()
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.join(timeout=5)

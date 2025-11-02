# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
M4S HLS Recorder

This module handles HLS streams that use fragmented MP4 (.m4s) segments instead of
traditional transport stream (.ts) segments. It downloads segments and feeds them
directly to FFmpeg for processing and recording.
"""

from __future__ import annotations

import os
import time
import subprocess
from base_recorder import BaseRecorder
from debug import get_logger


logger = get_logger(__file__)


class HLS_Recorder_M4S(BaseRecorder):
    """
    HLS Recorder for fragmented MP4 (M4S) segments
    """

    def __init__(self):
        """Initialize the M4S HLS recorder"""
        super().__init__(self.__class__.__name__)
        self.ffmpeg_process = None

    def record_start(self, resolve_result):
        """
        Start M4S HLS recording - BaseRecorder handles threading

        Args:
            resolve_result (dict): Complete resolve result containing resolved_url,
                                 rec_dir, show_ads, ffmpeg_headers,
                                 original_url, etc.
        """
        super().record_start(resolve_result)

        # Store resolve_result for access to configuration options
        self.resolve_result = resolve_result

        # Extract data from resolve_result
        channel_uri = resolve_result.get("resolved_url")
        ffmpeg_headers = resolve_result.get("ffmpeg_headers")
        rec_dir = resolve_result.get("rec_dir", "/tmp")

        # Validate required inputs
        if not channel_uri:
            raise ValueError("No channel URI provided for M4S HLS recording")

        logger.info("Starting M4S HLS recording for: %s", channel_uri)
        logger.info("Extracted ffmpeg_headers: %s", type(ffmpeg_headers))
        logger.info("Extracted ffmpeg_headers content: %s", ffmpeg_headers)

        # Start recording - BaseRecorder handles the threading
        self.record_stream(channel_uri, rec_dir, ffmpeg_headers)

    def record_stream(self, channel_uri, rec_dir, ffmpeg_headers=None):
        """
        Record M4S-based HLS stream with FFmpeg

        Args:
            channel_uri (str): HLS playlist URL
            rec_dir (str): Output directory for recorded files
            ffmpeg_headers (str, optional): Authentication headers to pass to FFmpeg
        """
        logger.info("M4S recorder starting for URI: %s", channel_uri)

        count_up = 0

        try:
            output_file = os.path.join(rec_dir, "stream_0.ts")

            # FFmpeg will handle URL validation and authentication directly

            self.ffmpeg_process = self._start_ffmpeg(channel_uri, output_file, ffmpeg_headers)
            logger.info("M4S recording started - FFmpeg handling stream")

            # Wait for FFmpeg to complete or stop signal
            while not self.stop_event.is_set():
                if self.ffmpeg_process.poll() is not None:
                    # FFmpeg process ended - capture stderr for error details
                    return_code = self.ffmpeg_process.returncode

                    # Get FFmpeg stderr output for error diagnosis
                    try:
                        _stdout, stderr = self.ffmpeg_process.communicate(timeout=5)
                        if stderr:
                            stderr_text = stderr.decode('utf-8', errors='ignore')
                            logger.error("FFmpeg stderr (full): %s", stderr_text)

                            # Look for specific error patterns
                            if "403" in stderr_text or "Forbidden" in stderr_text:
                                logger.error("FFmpeg got 403 Forbidden - authentication failed")
                            elif "404" in stderr_text or "Not Found" in stderr_text:
                                logger.error("FFmpeg got 404 Not Found - URL invalid or expired")
                            elif "401" in stderr_text or "Unauthorized" in stderr_text:
                                logger.error("FFmpeg got 401 Unauthorized - credentials invalid")
                            elif "Invalid data" in stderr_text:
                                logger.error("FFmpeg got invalid data - URL may not be a valid stream")
                            elif "Protocol not found" in stderr_text or "protocol" in stderr_text.lower():
                                logger.error("FFmpeg protocol error - check URL format and network connectivity")
                    except Exception as e:
                        logger.warning("Could not get FFmpeg stderr: %s", e)
                        raise

                    if return_code == 0:
                        logger.info("FFmpeg completed successfully")
                    else:
                        logger.error("FFmpeg exited with code: %d", return_code)

                        # Specific diagnosis for common exit codes
                        if return_code == 234:
                            logger.error("Exit code 234: Invalid data or protocol error")
                            logger.error("Possible causes: expired URL token, invalid M3U8 format, network issues")
                        elif return_code == 1:
                            logger.error("Exit code 1: General FFmpeg error - check stderr above")
                        elif return_code in {403, 404}:
                            logger.error("Exit code %d: HTTP error - authentication or URL issues", return_code)

                        if self.socketserver:
                            self.socketserver.broadcast([
                                "stop", {
                                    "reason": "error",
                                    "message": f"FFmpeg failed with exit code {return_code}",
                                    "channel": channel_uri,
                                    "rec_dir": rec_dir
                                }
                            ])
                    break

                if count_up == 5:
                    self.start_playback(channel_uri, output_file, "hls_m4s")
                count_up += 1

                time.sleep(1)  # Check every second

        except Exception as e:
            logger.error("M4S HLS recording error: %s", e)
            raise

        finally:
            self._cleanup()
            logger.info("M4S HLS recording stopped")

    def _start_ffmpeg(self, input_url, output_file, ffmpeg_headers=None):
        """
        Start FFmpeg process to handle M3U8 HLS stream directly

        Args:
            input_url (str): M3U8 playlist URL
            output_file (str): Output file path
            ffmpeg_headers (str): Headers to pass to FFmpeg

        Returns:
            subprocess.Popen: FFmpeg process object
        """
        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-y',
            '-reconnect', '1',       # Auto-reconnect on connection loss
            '-reconnect_streamed', '1',  # Reconnect for streamed content
            '-reconnect_delay_max', '30',  # Max reconnection delay
        ]
        if ffmpeg_headers:
            cmd += ['-headers', ffmpeg_headers]

        # Universal transcoding approach for maximum Dreambox compatibility
        logger.info("Transcoding all sources to optimized H.264 for maximum Dreambox compatibility")

        # TS output format - better for older Dreambox hardware
        cmd += [
            '-i', input_url,
            '-c:v', 'libx264',       # Transcode to H.264
            '-profile:v', 'baseline',  # Baseline profile for maximum compatibility
            '-level:v', '3.1',       # Level 3.1 for 720p HD support
            '-preset', 'ultrafast',  # Use ultrafast preset for real-time transcoding
            '-tune', 'zerolatency',  # Optimize for low latency (auto-optimizes GOP size)
            '-threads', '0',         # Use all available CPU cores
            '-x264opts', 'bframes=0:cabac=0:weightp=0:8x8dct=0:aud=1:me=dia:subme=1:trellis=0:ref=1:slices=8',  # Consolidated encoding options
            '-c:a', 'aac',           # AAC audio
            '-bsf:a', 'aac_adtstoasc',  # Fix AAC bitstream format
            '-f', 'mpegts',          # Transport Stream format
            '-mpegts_copyts', '1',   # Copy timestamps
            '-mpegts_start_pid', '0x100',  # Set consistent PID numbering
            '-mpegts_m2ts_mode', '0',  # Disable M2TS mode
            '-mpegts_original_network_id', '1',  # Set network ID
            '-mpegts_service_id', '1',  # Set service ID
            '-fflags', '+genpts+igndts+flush_packets+discardcorrupt',  # Generate PTS, ignore discontinuities, discard corrupt packets
            '-max_muxing_queue_size', '512',  # Even smaller queue for lower latency
            '-avoid_negative_ts', 'make_zero',  # Ensure positive timestamps
            '-err_detect', 'ignore_err',  # Ignore minor stream errors
            '-vsync', 'cfr',         # Constant frame rate
            '-shortest',             # End when shortest stream ends
            '-probesize', '128M',    # Increase probe size
            '-analyzeduration', '20M',  # Analyze more data upfront
            '-loglevel', 'error',
            output_file
        ]

        # Log the complete command for debugging (but mask sensitive data)
        cmd_str_safe = []
        skip_next = False
        for i, arg in enumerate(cmd):
            if skip_next:
                # Mask the headers content for security
                cmd_str_safe.append('"[HEADERS_MASKED]"')
                skip_next = False
            elif arg == '-headers' and i + 1 < len(cmd):
                cmd_str_safe.append(arg)
                skip_next = True
            else:
                cmd_str_safe.append(f'"{arg}"' if ' ' in arg else arg)

        logger.info("Full FFmpeg command (headers masked): %s", ' '.join(cmd_str_safe))
        logger.info("Headers being passed to FFmpeg: %s", ffmpeg_headers)

        try:
            version_result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5, check=False)
            logger.info("FFmpeg version check result: %d", version_result.returncode)
        except Exception as e:
            logger.error("FFmpeg not accessible: %s", e)
            raise

        logger.info("Starting FFmpeg with M3U8 input and auth headers...")
        # Not using with context manager here because we need to return and manage the process
        # through its lifecycle in the calling method
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return process

    def _cleanup(self):
        """Clean up resources"""
        # Stop FFmpeg process
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=10)
            except Exception as e1:
                logger.debug("Error terminating FFmpeg process: %s", e1)
                try:
                    self.ffmpeg_process.kill()
                    self.ffmpeg_process.wait(timeout=5)
                except Exception as e2:
                    logger.debug("Error killing FFmpeg process: %s", e2)
            self.ffmpeg_process = None
            logger.info("FFmpeg process terminated")

    def on_thread_ended(self):
        """Called when recording thread ends - cleanup FFmpeg"""
        super().on_thread_ended()  # Call parent cleanup
        self._cleanup()
        logger.info("M4S HLS recording cleanup completed")

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
import threading
import subprocess
import glob
from debug import get_logger


logger = get_logger(__file__)


class HLS_Recorder_M4S:
    """
    HLS Recorder for fragmented MP4 (M4S) segments
    """

    def __init__(self):
        """Initialize the M4S HLS recorder"""
        self.is_running = False
        self.stop_event = threading.Event()
        self.channel_uri = ""
        self.socketserver = None
        self.session = None
        self.ffmpeg_process = None
        self.output_file = ""

    def start_playback(self):
        """
        Start playback after a delay - signals to the client that the stream is ready for playback
        This method is called by a timer after FFmpeg has started processing the stream
        """
        if self.socketserver:
            self.socketserver.broadcast(["start", {"url": self.channel_uri, "rec_file": self.output_file, "section_index": 0, "segment_index": 0}])
        else:
            logger.warning("Cannot start playback - no socket server or not running")

    def start(self, channel_uri, rec_dir, _show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        Start M4S HLS recording

        Args:
            channel_uri (str): HLS playlist URL
            rec_dir (str): Output directory
            buffering (int): Number of segments to buffer
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("Starting M4S HLS recording for: %s", channel_uri)

        self.stop()

        self.channel_uri = channel_uri

        # Clean up old files
        pattern = rec_dir + "/stream*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)

        while self.is_running:
            logger.info("Recording is still running. waiting...")
            time.sleep(0.5)

        self.stop_event.clear()
        threading.Thread(target=self.record_stream, args=(channel_uri, rec_dir, buffering, auth_tokens, original_page_url, all_sources), daemon=True).start()

    def record_stream(self, channel_uri, rec_dir, _buffering, auth_tokens=None, original_page_url=None, _all_sources=None):
        """
        Record M4S-based HLS stream with FFmpeg

        Args:
            channel_uri (str): HLS playlist URL
            rec_dir (str): Output directory for recorded files
            auth_tokens (AuthTokens | None): Authentication tokens for protected streams
            original_page_url (str | None): Original page URL for Referer header
        """
        logger.info("M4S recorder starting for URI: %s", channel_uri)

        self.is_running = True
        count_up = 0

        try:
            # Use different output formats based on source type
            if '.av1.mp4.m3u8' in channel_uri:
                self.output_file = output_file = os.path.join(rec_dir, "stream_0.ts")
                logger.info("Using TS output for AV1 transcoding (better Dreambox compatibility)")
            else:
                self.output_file = output_file = os.path.join(rec_dir, "stream_0.mp4")
                logger.info("Using MP4 output for better player compatibility")

            # FFmpeg will handle URL validation and authentication directly

            # Get authentication headers from centralized AuthTokens utility
            if auth_tokens and hasattr(auth_tokens, 'format_for_ffmpeg'):
                ffmpeg_headers = auth_tokens.format_for_ffmpeg(original_page_url)
                logger.info("Using centralized AuthTokens for FFmpeg headers")
            else:
                # Minimal fallback headers if no auth tokens available
                logger.warning("No centralized auth_tokens available - using minimal headers")
                default_headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                }
                if original_page_url:
                    default_headers['Referer'] = original_page_url
                header_lines = [f"{k}: {v}" for k, v in default_headers.items()]
                ffmpeg_headers = "\r\n".join(header_lines)

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
                    except Exception as e:
                        logger.warning("Could not get FFmpeg stderr: %s", e)

                    if return_code == 0:
                        logger.info("FFmpeg completed successfully")
                    else:
                        logger.error("FFmpeg exited with code: %d", return_code)

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

                if count_up == 10:

                    self.start_playback()
                count_up += 1

                time.sleep(1)  # Check every second

        except Exception as e:
            logger.error("M4S HLS recording error: %s", e)
            if self.socketserver:
                self.socketserver.broadcast([
                    "stop", {
                        "reason": "error",
                        "channel": channel_uri,
                        "rec_dir": rec_dir
                    }
                ])
        finally:
            self._cleanup()
            self.is_running = False
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
        # Build FFmpeg command - always use MP4 output for better compatibility
        cmd = [
            'ffmpeg',
            '-y',
        ]
        if ffmpeg_headers:
            cmd += ['-headers', ffmpeg_headers]

        # Intelligent codec detection and handling
        # Check source codec and apply appropriate transcoding strategy
        # Detect source video codec from URL patterns
        is_av1_source = '.av1.mp4.m3u8' in input_url
        is_h265_source = '.h265.mp4.m3u8' in input_url or '.hevc.mp4.m3u8' in input_url
        is_vp9_source = '.vp9.mp4.m3u8' in input_url
        is_h264_source = '.h264.mp4.m3u8' in input_url or '.avc.mp4.m3u8' in input_url

        if is_av1_source:
            logger.info("Detected AV1 source - transcoding to H.264 for Dreambox compatibility")
            # Use .ts container for better Dreambox compatibility and avoid MP4 container issues
            output_file_ts = output_file.replace('.mp4', '.ts')
            cmd += [
                '-i', input_url,
                '-c:v', 'libx264',       # Transcode AV1 to H.264 for older GStreamer compatibility
                '-profile:v', 'baseline',  # Use Baseline profile for maximum compatibility with older hardware
                '-level:v', '3.0',       # Level 3.0 (most conservative)
                '-preset', 'ultrafast',  # Use ultrafast preset for real-time transcoding
                '-b:v', '1500k',         # Better bitrate for 720p24 quality
                '-maxrate', '1500k',     # Set max bitrate
                '-bufsize', '3000k',     # Set buffer size (2x bitrate)
                '-pix_fmt', 'yuv420p',   # Ensure compatible pixel format
                '-vf', 'scale=1280:720:flags=fast_bilinear',  # Use fast scaling for speed
                '-r', '24',              # Match source frame rate (720p24)
                '-g', '48',              # GOP size 2 seconds (24fps * 2)
                '-refs', '1',            # Limit reference frames for older decoder compatibility
                '-tune', 'zerolatency',  # Optimize for low latency streaming
                '-slices', '8',          # More slices for parallel processing
                '-threads', '0',         # Use all available CPU cores (0 = auto)
                '-x264opts', 'bframes=0:cabac=0:weightp=0:8x8dct=0:aud=1:me=dia:subme=1:trellis=0',  # Ultra-basic encoding options
                '-c:a', 'aac',           # Transcode audio to AAC for Dreambox compatibility
                '-bsf:a', 'aac_adtstoasc',  # Fix AAC bitstream format for container compatibility
                '-b:a', '128k',          # Set audio bitrate to 128kbps
                '-ar', '48000',          # Ensure 48kHz sample rate
                '-ac', '2',              # Force stereo audio
                '-f', 'mpegts',          # Use Transport Stream format (better for Dreambox)
                '-mpegts_copyts', '1',   # Copy timestamps for better TS compatibility
                '-mpegts_start_pid', '0x100',  # Set consistent PID numbering
                '-mpegts_m2ts_mode', '0',  # Disable M2TS mode for better compatibility
                '-mpegts_pmt_start_pid', '0x1000',  # Set PMT PID for better compatibility
                '-mpegts_original_network_id', '1',  # Set network ID for proper TS structure
                '-mpegts_service_id', '1',  # Set service ID
                '-muxrate', '2000000',   # Set constant mux rate (2 Mbps) for steady stream
                '-flush_packets', '1',   # Flush packets immediately for better streaming
                '-fflags', '+genpts+igndts',  # Generate PTS, ignore DTS discontinuities, low delay mode
                '-max_muxing_queue_size', '1024',  # Smaller queue for lower latency
                '-max_delay', '0',       # Minimize encoding delay
                '-avoid_negative_ts', 'make_zero',  # Ensure positive timestamps
                '-vsync', 'cfr',          # Constant frame rate to avoid timing gaps
                '-shortest',              # End when shortest stream ends (prevents hanging)
                '-copyts',                # Copy input timestamps (better sync)
                '-probesize', '256M',     # Increase probe size for better initial analysis
                '-analyzeduration', '40M',  # Analyze more data upfront for smoother start
                '-loglevel', 'error',
                # No time limit - record the entire video
                output_file_ts
            ]
        elif is_h265_source:
            logger.info("Detected H.265/HEVC source - transcoding to H.264 for Dreambox compatibility")
            # H.265 -> H.264 transcoding (older Dreambox may not support H.265)
            output_file_ts = output_file.replace('.mp4', '.ts')
            cmd += [
                '-i', input_url,
                '-c:v', 'libx264',       # Transcode H.265 to H.264
                '-profile:v', 'main',    # Use Main profile for H.265->H.264 (better than Baseline)
                '-level:v', '4.0',       # Level 4.0 for good compatibility
                '-preset', 'fast',       # Fast preset for H.265 transcoding
                '-crf', '23',            # Good quality for H.265->H.264
                '-pix_fmt', 'yuv420p',   # Ensure compatible pixel format
                '-c:a', 'copy',          # Copy audio (likely already compatible)
                '-f', 'mpegts',          # Use TS format
                '-loglevel', 'error',
                output_file_ts
            ]
        elif is_vp9_source:
            logger.info("Detected VP9 source - transcoding to H.264 for Dreambox compatibility")
            # VP9 -> H.264 transcoding
            output_file_ts = output_file.replace('.mp4', '.ts')
            cmd += [
                '-i', input_url,
                '-c:v', 'libx264',       # Transcode VP9 to H.264
                '-profile:v', 'main',    # Use Main profile
                '-level:v', '4.0',       # Level 4.0
                '-preset', 'medium',     # Medium preset for VP9 transcoding
                '-crf', '22',            # Good quality
                '-pix_fmt', 'yuv420p',   # Compatible pixel format
                '-c:a', 'copy',          # Copy audio
                '-f', 'mpegts',          # Use TS format
                '-loglevel', 'error',
                output_file_ts
            ]
        elif is_h264_source:
            logger.info("Detected H.264 source - optimizing for Dreambox compatibility")
            # H.264 source - check if we need to re-encode for compatibility
            output_file_ts = output_file.replace('.mp4', '.ts')
            cmd += [
                '-i', input_url,
                '-c:v', 'libx264',       # Re-encode to ensure Baseline profile
                '-profile:v', 'baseline',  # Force Baseline for maximum compatibility
                '-level:v', '4.0',       # Level 4.0
                '-preset', 'veryfast',   # Fast preset since source is already H.264
                '-crf', '20',            # High quality (minimal quality loss)
                '-pix_fmt', 'yuv420p',   # Ensure compatible pixel format
                '-c:a', 'copy',          # Copy audio
                '-f', 'mpegts',          # Use TS format
                '-loglevel', 'error',
                output_file_ts
            ]
        else:
            logger.info("Unknown codec or using copy mode for compatible source")
            # Fallback: copy mode for unknown or already compatible sources
            cmd += [
                '-i', input_url,
                '-c:v', 'copy',          # Copy video stream as-is (preserves quality)
                '-c:a', 'copy',          # Copy audio stream as-is (preserves quality)
                '-movflags', '+frag_keyframe+empty_moov+faststart',  # Fragmented MP4 for real-time streaming + faststart for final file
                '-f', 'mp4',  # Force MP4 container (universally compatible)
                '-loglevel', 'error',
                # No time limit - record the entire video
                output_file
            ]
        logger.info("Using updated FFmpeg command with all headers")
        cmd_str = ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in cmd)
        logger.info("DEBUG: Full FFmpeg command: %s", cmd_str)
        try:
            version_result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5, check=False)
            logger.info("DEBUG: FFmpeg version check result: %d", version_result.returncode)
        except Exception as e:
            logger.error("DEBUG: FFmpeg not accessible: %s", e)
        logger.info("Starting FFmpeg with M3U8 input and all auth headers: %s", ' '.join(cmd[:8]) + '...')
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

    def stop(self):
        """Stop the recording"""
        logger.info("Stopping M4S HLS recording...")
        self.stop_event.set()

"""
HLS Segment Processing Utilities

This module provides a collection of utility functions for handling individual
HLS (HTTP Live Streaming) segments. It includes capabilities for downloading,
decrypting, validating, processing (e.g., scaling), and analyzing media segments.
These functions are essential components of the HLS recording and processing pipeline.
"""
from __future__ import annotations
import os
import time
import subprocess
import json
import tempfile
import requests

from crypt_utils import decrypt_segment, get_encryption_info
from ts_utils import is_valid_ts_segment
from debug import get_logger

logger = get_logger(__file__)


def download_segment(
    session: requests.Session,
    segment_url: str,
    segment_sequence: int,
    segment_encryption_info: dict,
    max_retries: int,
    timeout: float
) -> bytes | None:
    """
    Downloads and decrypts a single HLS segment with retries.

    This function attempts to download a segment from the given URL. If the
    segment is encrypted (as indicated by `segment_encryption_info`), it fetches
    the decryption key and decrypts the segment data. It includes a retry
    mechanism to handle transient network errors.

    Args:
        session: A `requests.Session` object for making HTTP requests.
        segment_url: The URL of the HLS segment to download.
        segment_sequence: The sequence number of the segment, used for logging.
        segment_encryption_info: A dictionary containing encryption details
                                 (METHOD, URI, IV) from the playlist.
        max_retries: The maximum number of download attempts.
        timeout: The timeout in seconds for the HTTP request.

    Returns:
        The decrypted segment data as bytes, or None if the download or
        decryption fails after all retries.
    """

    current_key = get_encryption_info(session, segment_encryption_info)

    attempt = 0
    while attempt < max_retries:
        try:
            logger.debug("🔽 Downloading segment %s: %s (attempt %s)",
                         segment_sequence, os.path.basename(segment_url), attempt + 1)
            response = session.get(segment_url, allow_redirects=True, timeout=timeout)
            response.raise_for_status()
            segment_data = response.content
            # Decrypt if necessary (use playlist-provided key/iv/method)
            if current_key["METHOD"] == 'AES-128' and current_key.get("KEY"):
                decrypted = decrypt_segment(segment_data, segment_sequence, None, current_key)
                if decrypted is None:
                    logger.debug("🗑️ Skipping segment %s: decryption failed", segment_sequence)
                    return None
                segment_data = decrypted
            return segment_data
        except Exception as e:
            logger.debug("❌ Error downloading segment (attempt %s): %s", attempt + 1, e)
            attempt += 1
            time.sleep(1)  # Short delay before retry
    logger.error("❌ Failed to download segment %s after %s attempts.", segment_sequence, max_retries)
    return None


def append_to_rec_file(
    rec_file: str,
    segment_data: bytes,
    org_segment_data: bytes,
    current_uri: str,
    segment_index: int
) -> None:
    """
    Appends segment data to a recording file and saves related debug files.

    This function writes the processed `segment_data` to the main recording file.
    For debugging purposes, it also:
    - Logs the segment's URI to a .log file.
    - Saves the processed segment to its own .ts file.
    - Saves the original, unprocessed segment to its own _org.ts file.

    Args:
        rec_file: The path to the main recording file.
        segment_data: The processed binary segment data to append.
        org_segment_data: The original, unprocessed segment data for debugging.
        current_uri: The URI of the segment, used for logging.
        segment_index: The index of the segment, used for logging and filenames.
    """

    log_file = os.path.splitext(rec_file)[0] + '.log'
    uri_name = os.path.basename(current_uri)
    pkt_file = os.path.splitext(rec_file)[0] + f"_{segment_index}.ts"
    org_pkt_file = os.path.splitext(rec_file)[0] + f"_{segment_index}_org.ts"
    try:
        if not is_valid_ts_segment(segment_data):
            logger.error("✗ Invalid TS segment data for %s", current_uri)

        with open(rec_file, 'ab') as rec_f:
            rec_f.write(segment_data)
            rec_f.flush()

        with open(log_file, 'a', encoding="utf-8") as log_f:
            log_f.write(f"{segment_index}: {uri_name}\n")

        if False:
            with open(pkt_file, 'wb') as pkt_f:
                pkt_f.write(segment_data)

            with open(org_pkt_file, 'wb') as org_pkt_f:
                org_pkt_f.write(org_segment_data)

        # Log segment info
        file_size = os.path.getsize(rec_file) / (1024 * 1024)
        logger.info("✓ Appended segment %s, %s to %s %.2f MB",
                    segment_index, uri_name, rec_file, file_size)
        logger.info("=" * 70)

    except Exception as e:
        logger.error("❌ Error appending to output file: %s", e)


def scale_segment(
    segment_data: bytes,
    resolution: tuple[int, int],
    vid_pid: int,
    aud_pid: int
) -> bytes:
    """
    Scales a MPEG-TS segment using FFmpeg and remaps PIDs.

    This function takes raw MPEG-TS segment data, scales the video stream to the
    specified resolution, and re-maps the video and audio Packet IDs (PIDs).
    It uses the H.264 High profile for better encoding quality. The audio
    stream is copied without re-encoding.

    Args:
        segment_data: The raw bytes of the input MPEG-TS segment.
        resolution: A tuple (width, height) for the target video resolution.
        vid_pid: The new PID for the video stream.
        aud_pid: The new PID for the audio stream.

    Returns:
        The processed MPEG-TS segment data as bytes.

    Raises:
        RuntimeError: If the FFmpeg process fails.
    """
    width, height = resolution

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",  # keep only errors in stderr
        "-y",
        "-f", "mpegts",        # input format
        "-i", "pipe:0",        # read from stdin
        "-vf", f"scale={width}:{height}",
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",

        # Force High profile with all necessary settings
        "-profile:v", "high",            # High profile
        "-level:v", "4.1",               # Level 4.1 (supports 1080p at 30fps)
        "-x264-params", "profile=high",  # Redundant but ensures x264 gets it
        "-b_strategy", "1",              # Enable B-frames (not in Baseline)
        "-bf", "3",                      # Use up to 3 B-frames between I and P frames
        "-flags", "+cgop",               # Closed GOP, better seeking
        "-coder", "1",                   # CABAC entropy coding (not in Baseline)
        "-8x8dct", "1",                  # Enable 8x8 transform (High profile feature)
        "-partitions", "i8x8,i4x4,p8x8,b8x8",  # Use all partition types

        "-crf", "23",
        "-c:a", "copy",
        "-f", "mpegts",
        "-streamid", f"0:{vid_pid}",
        "-streamid", f"1:{aud_pid}",
        "pipe:1"                         # write to stdout
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=segment_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else "<no stderr>"
        raise RuntimeError(f"ffmpeg failed (exit {e.returncode}):\n{stderr}") from e

    return proc.stdout


def is_filler_segment(uri: str) -> bool:
    """
    Checks if a segment URI corresponds to filler, ad, or error content.

    This is determined by checking for the presence of specific substrings
    within the URI that are commonly used by PlutoTV for such content.

    Args:
        uri: The URI of the segment to check.

    Returns:
        True if the URI matches a known filler signature, False otherwise.
    """
    filler_signatures = ["_plutotv_error_", "_plutotv_filler_", "Well_be_right_back_", "_ad/", "_ad_"]
    return any(sig in uri for sig in filler_signatures)


def get_segment_properties(segment_data: bytes) -> tuple[str | None, float | None, int | None]:
    """
    Extracts video properties from segment data using ffprobe.

    This function analyzes a media segment to determine its resolution, duration,
    and the presentation timestamp (PTS) of the first frame. It writes the segment
    to a temporary file to ensure ffprobe can analyze it reliably.

    Args:
        segment_data: The raw bytes of the MPEG-TS segment.

    Returns:
        A tuple containing:
        - The resolution as a string (e.g., "1920x1080").
        - The duration in seconds (float).
        - The first PTS value (int).
        If any property cannot be determined, its value will be None.
    """
    with tempfile.NamedTemporaryFile(delete=True, suffix=".ts", mode='wb') as temp_segment_file:
        temp_segment_file.write(segment_data)
        temp_segment_file.flush()  # Ensure all data is written to disk
        segment_path = temp_segment_file.name

        ffprobe_path = "ffprobe"  # Assumes ffprobe is in the system's PATH
        command = [
            ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            segment_path,  # Analyze the file directly
        ]
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                check=True,
                timeout=10
            )

            # ffprobe can sometimes output non-JSON text on stdout before the JSON data.
            # We'll find the start of the JSON, clean null bytes, and parse from there.
            output_str = process.stdout.decode('utf-8', errors='ignore')
            # print(f"ffprobe output: {output_str}")
            json_start_index = output_str.find('{')

            if json_start_index == -1:
                logger.error("❌ No JSON object found in ffprobe output.")
                logger.debug("ffprobe stdout: %s", output_str)
                return None, None, None

            clean_json_str = output_str[json_start_index:].replace('\x00', '')

            try:
                probe_data = json.loads(clean_json_str)
            except json.JSONDecodeError as e:
                logger.error("❌ Failed to decode ffprobe's JSON output: %s", e)
                logger.debug("Problematic JSON string for parsing: %s", clean_json_str)
                return None, None, None

            # Extract resolution from the first video stream
            video_stream = next((s for s in probe_data.get('streams', []) if s.get('codec_type') == 'video'), None)
            resolution = None
            if video_stream and 'width' in video_stream and 'height' in video_stream:
                resolution = f"{video_stream['width']}x{video_stream['height']}"
                logger.debug("✓ Detected segment resolution: %s", resolution)

            # Extract duration - prioritize 'format' duration, fall back to video stream duration
            duration = None
            duration_str = probe_data.get('format', {}).get('duration')
            if duration_str:
                try:
                    duration = round(float(duration_str)) * 90000
                    logger.debug("✓ Detected format duration: %ss", duration)
                except (ValueError, TypeError):
                    logger.warning("Could not parse format duration from ffprobe: '%s'", duration_str)

            # Fallback to video stream duration if format duration is not found
            if duration is None and video_stream:
                duration_str = video_stream.get('duration')
                if duration_str:
                    try:
                        duration = round(float(duration_str)) * 90000
                        logger.debug("✓ Detected video stream duration: %ss", duration)
                    except (ValueError, TypeError):
                        logger.warning("Could not parse video stream duration from ffprobe: '%s'", duration_str)

            # Extract first PTS from the video stream
            first_pts = None
            if video_stream:
                start_pts_str = video_stream.get('start_pts')
                if start_pts_str:
                    try:
                        first_pts = int(start_pts_str)
                        logger.debug("✓ Detected start_pts: %s", first_pts)
                    except (ValueError, TypeError):
                        logger.warning("Could not parse start_pts from ffprobe: '%s'", start_pts_str)

            if resolution or duration is not None or first_pts is not None:
                return resolution, duration, first_pts

            logger.warning("Could not determine resolution, duration, or PTS from ffprobe.")
            return None, None, None

        except FileNotFoundError:
            logger.error("❌ ffprobe not found. Please ensure it's installed and in your system's PATH.")
            return None, None, None
        except subprocess.TimeoutExpired:
            logger.error("❌ ffprobe command timed out after 10 seconds.")
            return None, None, None
        except subprocess.CalledProcessError as e:
            stderr_output = e.stderr.decode('utf-8', errors='ignore').strip()
            logger.error("❌ ffprobe failed with exit code %s: %s", e.returncode, stderr_output)
            return None, None, None
        except Exception as e:
            logger.error("❌ An unexpected error occurred while getting segment properties: %s", e)
            return None, None, None

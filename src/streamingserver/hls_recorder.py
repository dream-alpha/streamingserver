# Copyright (c) 2018 - 2025 dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Segment Downloader and Recorder

This script provides the core functionality for downloading HLS (HTTP Live Streaming)
streams, processing the segments, and recording them to a file. It handles
playlist parsing, segment downloading, decryption, and timestamp correction to
create a continuous MPEG-TS file.

The recording process can be controlled via a socket-based command server,
allowing for dynamic start and stop commands.
"""

import os
import re
import time
import threading
import traceback
import glob
import subprocess
import m3u8

from socket_server import SocketServer
from hls_playlist_utils import get_master_playlist, get_playlist, different_uris
from ffmpeg_utils import close_ffmpeg_process, open_ffmpeg_process, terminate_ffmpeg_process, write_ffmpeg_segment
from log_utils import write_log
from ts_utils import shift_segment, is_valid_ts_segment, set_discontinuity_segment, update_continuity_counters
from hls_segment_utils import append_to_rec_file, get_segment_properties, download_segment, is_filler_segment
from session_utils import get_session
from debug import get_logger

logger = get_logger(__file__)


class HLS_Recorder:
    socketserver: SocketServer | None

    """
    Manages the HLS recording lifecycle for a single stream.

    This class handles the entire process from fetching the master playlist to
    downloading and processing individual segments. It maintains the recording
    state, manages a persistent HTTP session, and orchestrates the complex
    tasks of timestamp synchronization and discontinuity handling.

    Attributes:
        is_running (bool): True if a recording is currently active.
        _stop_event (threading.Event): Event used to signal the recording loop to stop.
        channel_uri (str): The URI of the HLS stream being recorded.
        socketserver (SocketServer): A reference to the command server.
        playlist_processor (HLSPlaylistProcessor): The processor for parsing and diffing playlists.
        session (requests.Session): The session object for making HTTP requests.
    """

    def __init__(self):
        """Initializes the HLS_Recorder instance."""
        self.is_running = False
        self._stop_event = threading.Event()
        self.channel_uri = ""
        self.socketserver = None
        self.playlist_processor = None
        self.session = None

    def record_stream(self, channel_uri, rec_file):
        """
        The main recording loop for an HLS stream.
        """
        logger.info("channel_uri: %s, rec_file: %s", channel_uri, rec_file)
        self.is_running = True

        rec_dir = os.path.dirname(rec_file)
        segment_index = 0
        previous_segment_index = -1
        section_index = -1
        previous_uri = ""
        previous_duration = 0
        previous_pts = 0
        current_resolution = None
        previous_resolution = None
        offset = 0
        continuous_pts = 0
        empty_playlist_count = 0
        max_empty_playlists = 10
        failed_playlist_count = 0
        max_failed_playlists = 5
        cc_map = {}
        monotonize_segment = False
        reload_master_playlist = True
        media_playlist_url = None
        section_file = os.path.splitext(rec_file)[0] + "_0.ts"
        ffmpeg_proc = None
        last_sequence = None
        previous_filler = None
        current_filler = None
        _target_duration = 5

        try:
            while not self._stop_event.is_set():
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

                _target_duration = playlist.target_duration  # pylint: disable=no-member

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
                    if self._stop_event.is_set():
                        logger.info("Recording stopped by user")
                        break

                    sequence = sequence_start + idx
                    if last_sequence is not None and sequence <= last_sequence:
                        continue  # Already processed
                    last_sequence = sequence

                    logger.info("Segment: %s: %s", segment_index, segment.uri)

                    new_section = False

                    key_info = {"METHOD": None, "URI": None, "IV": None}
                    if segment.key:
                        key_info = {"METHOD": segment.key.method, "URI": segment.key.uri, "IV": segment.key.iv}
                    segment_data = download_segment(self.session, segment.uri, segment_index, key_info, max_retries=10, timeout=5)

                    if not segment_data or not is_valid_ts_segment(segment_data):
                        logger.error("Failed to download segment or invalid ts segment %s", segment_index)
                        continue

                    current_resolution, current_duration, current_pts, _vpids, _apids = get_segment_properties(segment_data)

                    if current_pts is None:
                        raise ValueError(f"No PTS found in segment {segment_index}")

                    if different_uris(previous_uri, segment.uri):
                        logger.info("Prev URI: %s", previous_uri)
                        logger.info(">" * 70)
                        logger.info("Next URI: %s", segment.uri)

                        if previous_resolution != current_resolution:
                            logger.info("Resolution changed: %s > %s", previous_resolution, current_resolution)
                            write_log(rec_dir, segment.uri, section_index, segment_index, msg="resolution-change")
                            new_section = True

                        current_filler = is_filler_segment(segment.uri)
                        if current_filler != previous_filler:
                            logger.info("Filler changed: current_filler: %s, %s", current_filler, segment.uri)
                            write_log(rec_dir, segment.uri, section_index, segment_index, msg="filler-change")
                            new_section = True

                        monotonize_segment = current_filler

                    if new_section:
                        if previous_filler and previous_segment_index < 2:
                            logger.info("Inserting bumper file before new section")
                            write_log(rec_dir, previous_uri, section_index, previous_segment_index, msg="bumper-file")
                            bumper_file = "/data/ubuntu/root/plugins/streamingserver/data/ad2_0.ts"
                            self.socketserver.broadcast({"command": "start", "args": [previous_uri, bumper_file, section_index, -1]})

                    if new_section:
                        logger.info("=" * 70)
                        write_log(rec_dir, segment.uri, section_index, segment_index, msg="new-section\n")
                        close_ffmpeg_process(ffmpeg_proc)

                        segment_index = 0
                        section_index += 1
                        continuous_pts = current_pts
                        offset = 0
                        cc_map = {}
                        section_file = f"{os.path.splitext(rec_file)[0]}_{section_index}.ts"

                        if not current_filler:
                            ffmpeg_proc = open_ffmpeg_process(section_file)
                    else:
                        continuous_pts += previous_duration
                        offset = continuous_pts - current_pts

                    logger.debug("Timestamps %s: %s, Previous duration: %s,  Current PTS: %s, Continuous PTS: %s, Offset: %s", segment_index, previous_pts, previous_duration, current_pts, continuous_pts, offset)

                    if monotonize_segment:
                        segment_data = shift_segment(segment_data, offset)
                        segment_data, cc_map = update_continuity_counters(segment_data, cc_map)
                        write_log(rec_dir, segment.uri, section_index, segment_index, msg="monotonize")

                    if segment.discontinuity and current_filler:
                        logger.info("Discontinuity found in segment %s", segment_index)
                        segment_data = set_discontinuity_segment(segment_data)
                        write_log(rec_dir, segment.uri, section_index, segment_index, msg="discontinuity")

                    if not current_filler:
                        write_ffmpeg_segment(ffmpeg_proc, segment_data)
                        write_log(rec_dir, segment.uri, section_index, segment_index, msg="ffmpeg-segment")
                    else:
                        append_to_rec_file(section_file, segment_data)
                        write_log(rec_dir, segment.uri, section_index, segment_index, msg="filler-segment")
                    logger.info("Writing segment %s, %s to %s", segment_index, segment.uri, section_file)

                    if hasattr(self, 'socketserver'):
                        self.socketserver.broadcast({"command": "start", "args": [segment.uri, section_file, section_index, segment_index]})

                    previous_segment_index = segment_index
                    segment_index += 1
                    previous_uri = segment.uri
                    previous_duration = current_duration
                    previous_pts = current_pts
                    previous_resolution = current_resolution
                    previous_filler = current_filler

        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")
        except Exception as e:
            logger.error("Recording error: %s", e)
            self.socketserver.broadcast({"command": "stop", "args": ["error", self.channel_uri, rec_file]})
            traceback.print_exc()
        finally:
            terminate_ffmpeg_process(ffmpeg_proc)
            self.is_running = False
            logger.info("Recording stopped")

    def start(self, channel_uri, rec_file, show_ads=False):
        """
        Starts the recording process in a new thread.

        This method sets up the recording environment, cleans up old files,
        and launches the `record_stream` method in a background thread to
        avoid blocking.

        Args:
            channel_uri (str): The channel ID or full URL to record.
            rec_file (str): The path to the output recording file.
            show_ads (bool): Show ads (true) or fillers (false)
        """
        logger.info("#" * 70)
        logger.info("Starting HLS recording for channel: %s", channel_uri)
        logger.info("#" * 70)
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

        pattern = os.path.dirname(rec_file) + "/pluto*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
        logger.debug("Removed old files: %s", pattern)

        while self.is_running:
            logger.info("Recording is still running. waiting...")
            time.sleep(0.5)

        self._stop_event.clear()
        threading.Thread(target=self.record_stream, args=(self.channel_uri, rec_file), daemon=True).start()

    def stop(self):
        """Signals the recording thread to stop."""
        logger.info("Stopping recording...")
        self._stop_event.set()

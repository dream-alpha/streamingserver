#!/usr/bin/env python3
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
import argparse

from socket_server import CommandHandler, SocketServer
from hls_playlist_utils import get_master_playlist, get_playlist, different_uris
from ffmpeg_utils import close_ffmpeg_process, open_ffmpeg_process, terminate_ffmpeg_process, write_ffmpeg_segment
from log_utils import write_log
from ts_utils import shift_segment, is_valid_ts_segment, set_discontinuity_segment, update_continuity_counters
from hls_playlist import HLSPlaylistProcessor
from segment_utils import get_segment_properties, download_segment
from session_utils import get_session
from debug import get_logger


logger = get_logger(__file__)


class HLS_Recorder:
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
        segment_index = 0
        cumulative_segment_index = 0
        section_index = -1
        self.is_running = True
        previous_uri = None
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
        discontinuity = False
        section_file = os.path.splitext(rec_file)[0] + "_0.ts"
        ffmpeg_proc = None

        if self.playlist_processor is None:
            self.playlist_processor = HLSPlaylistProcessor()

        try:
            while not self._stop_event.is_set():
                if reload_master_playlist:
                    self.session = get_session()
                    media_playlist_url = get_master_playlist(self.session, channel_uri)
                    reload_master_playlist = False
                    write_log(section_file, "none", section_index, segment_index, msg="load-master-playlist")

                playlist = get_playlist(self.session, media_playlist_url)
                if not playlist:
                    logger.error("Failed to fetch playlist, retrying...")
                    failed_playlist_count += 1
                    if failed_playlist_count >= max_failed_playlists:
                        logger.error("Too many failed playlist fetches. Reloading master playlist...")
                        reload_master_playlist = True
                        continue
                    time.sleep(1)
                    continue
                failed_playlist_count = 0

                segment_list = self.playlist_processor.process(playlist)
                if not segment_list:
                    empty_playlist_count += 1
                    logger.debug("No new segments found, waiting for next playlist update...")
                    if empty_playlist_count >= max_empty_playlists:
                        logger.info("Playlist has been empty for too long. Reloading master playlist...")
                        reload_master_playlist = True
                        continue
                    time.sleep(1)
                    continue
                empty_playlist_count = 0

                logger.debug("Segment list has %s new segments", len(segment_list))
                for segment in segment_list:
                    logger.info("Segment list 1: %s: %s", segment_index, segment.uri)
                    logger.debug("Segment list 2: %s: %s", segment_index, segment.key_info.get('METHOD', 'none') if segment.key_info else 'none')
                    logger.debug("Segment list 3: %s: %s", segment_index, segment.duration)
                    if self._stop_event.is_set():
                        logger.info("Recording stopped by user")
                        break

                    if segment.endlist:
                        reload_master_playlist = True
                        break

                    new_section = False
                    discontinuity = False

                    segment_data = download_segment(self.session, segment.uri, segment_index, segment.key_info, max_retries=10, timeout=10)
                    # org_segment_data = segment_data

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
                            logger.info("Changing resolution: %s > %s", previous_resolution, current_resolution)
                            monotonize_segment = current_resolution not in ["1920x1080", "1280x720"]
                            new_section = True
                            discontinuity = True
                        else:
                            new_section = True
                            discontinuity = True

                    if new_section:
                        write_log(section_file, segment.uri, section_index, segment_index, msg="new-section\n")
                        close_ffmpeg_process(ffmpeg_proc, section_index)

                        segment_index = 0
                        section_index += 1
                        continuous_pts = current_pts
                        offset = 0
                        cc_map = {}
                        section_file = f"{os.path.splitext(rec_file)[0]}_{section_index}.ts"

                        ffmpeg_proc = open_ffmpeg_process(section_file, section_index)
                    else:
                        continuous_pts += previous_duration
                        offset = continuous_pts - current_pts

                    logger.debug("Timestamps %s: %s, Previous duration: %s,  Current PTS: %s, Continuous PTS: %s, Offset: %s", segment_index, previous_pts, previous_duration, current_pts, continuous_pts, offset)

                    if monotonize_segment:
                        segment_data = shift_segment(segment_data, offset)
                        segment_data, cc_map = update_continuity_counters(segment_data, cc_map)

                    if segment.discontinuity:
                        logger.info("Discontinuity found in segment %s", segment_index)
                        write_log(section_file, segment.uri, section_index, segment_index, msg="discontinuity")

                    if discontinuity or segment.discontinuity:
                        segment_data = set_discontinuity_segment(segment_data)

                    if ffmpeg_proc:
                        write_ffmpeg_segment(ffmpeg_proc, segment_data)
                        write_log(section_file, segment.uri, section_index, segment_index, msg="write segment")
                    else:
                        logger.error("No ffmpeg process available to write segment %s", segment_index)

                    if cumulative_segment_index == 5:
                        if hasattr(self, 'socketserver'):
                            self.socketserver.broadcast({"command": "start", "args": [self.channel_uri, section_file]})

                    segment_index += 1
                    cumulative_segment_index += 1
                    previous_uri = segment.uri
                    previous_duration = current_duration
                    previous_pts = current_pts
                    previous_resolution = current_resolution

        except KeyboardInterrupt:
            logger.info("⚠ Recording interrupted by user")
        except Exception as e:
            logger.error("❌ Recording error: %s", e)
            self.socketserver.broadcast({"command": "stop", "args": ["error", self.channel_uri, rec_file]})
            traceback.print_exc()
        finally:
            terminate_ffmpeg_process(ffmpeg_proc)
            self.is_running = False
            logger.info("✓ Recording stopped")

    def start(self, channel_uri, rec_file, show_ads=False):
        """
        Starts the recording process in a new thread.

        This method sets up the recording environment, cleans up old files,
        and launches the `record_stream` method in a background thread to
        avoid blocking.

        Args:
            channel_uri (str): The channel ID or full URL to record.
            rec_file (str): The path to the output recording file.
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
        logger.debug("📺 Using channel URI: %s", self.channel_uri)

        pattern = os.path.dirname(rec_file) + "/pluto*"
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
        logger.debug("🧹 Removed old files: %s", pattern)

        while self.is_running:
            logger.info("⚠️ Recording is still running. waiting...")
            time.sleep(0.5)

        self._stop_event.clear()
        threading.Thread(target=self.record_stream, args=(self.channel_uri, rec_file), daemon=True).start()

    def stop(self):
        """Signals the recording thread to stop."""
        logger.info("🛑 Stopping recording...")
        self._stop_event.set()


def main():
    """
    Main function to run the HLS recorder and command server.

    This function parses command-line arguments, initializes the HLS_Recorder,
    and starts a socket server to listen for commands. It can also start a
    recording immediately if a channel is provided via arguments.
    """
    logger.debug("🎬 HLS Recorder")

    parser = argparse.ArgumentParser(description="HLS Recorder")
    parser.add_argument('--rec_file', type=str, default='pluto.ts', help='File name for recording file (default: pluto.ts)')
    parser.add_argument('--channel', type=str, help='Channel ID')
    parser.add_argument('--show_ads', action='store_true', help='Whether to show ads (default: False)')
    args = parser.parse_args()

    recorder = HLS_Recorder()
    socketserver = None
    try:
        # Start socket server in background thread
        HOST, PORT = "0.0.0.0", 5000
        socketserver = SocketServer((HOST, PORT), CommandHandler, recorder)
        recorder.socketserver = socketserver
        socketserver_thread = threading.Thread(target=socketserver.serve_forever, daemon=True)
        socketserver_thread.start()
        logger.debug("🔌 Command socket server running on %s:%s", HOST, PORT)
        logger.debug("🚀 Ready for commands. Use 'start', 'stop' via socket.")
        if args.channel:
            logger.debug("Press Ctrl+C to exit.")
            recorder.start(args.channel, args.rec_file, args.show_ads)
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.debug("\n⚠️ Interrupted by user")
        recorder.stop()
        if socketserver is not None:
            socketserver.shutdown()
            socketserver.server_close()
    except Exception as e:
        logger.debug("❌ Unexpected error: %s", e)
        traceback.print_exc()
        recorder.stop()
        if socketserver is not None:
            socketserver.shutdown()
            socketserver.server_close()

    logger.debug("🏁 Recording session ended")


if __name__ == "__main__":
    main()

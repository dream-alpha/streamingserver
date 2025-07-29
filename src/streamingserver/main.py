#!/usr/bin/env python3
"""
HLS Segment Downloader
Downloads, decrypts segments segment-by-segment and stores them in <rec_dir>/pluto.ts
"""

import os
import sys
import time
import threading
import traceback
import argparse
import requests

from socket_server import RecorderCommandHandler, RecorderSocketServer
from hls_playlist_utils import get_master_playlist, get_playlist, different_uris
from crypt_utils import decrypt_segment, get_encryption_info
from ts_utils import shift_segment, read_pts_from_segment, is_valid_ts_segment, set_discontinuity_segment, update_continuity_counters, find_pat_pmt_in_segment
from hls_playlist import HLSPlaylistProcessor
from debug import get_logger


logger = get_logger(__name__, "DEBUG")


class HLS_Recorder:

    def __init__(self):
        self.is_running = False
        self._stop_event = threading.Event()
        self.channel = ""
        self.server = None

        self.playlist_processor = HLSPlaylistProcessor()
        # Session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://pluto.tv/',
            'Origin': 'https://pluto.tv',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })

    def download_segment(self, segment_url, segment_sequence, current_key, max_retries=3, timeout=10):
        """Download and decrypt a single HLS segment with retries"""
        attempt = 0
        while attempt < max_retries:
            try:
                logger.debug(f"🔽 Downloading segment {segment_sequence}: {os.path.basename(segment_url)} (attempt {attempt + 1})")
                response = self.session.get(segment_url, timeout=timeout)
                response.raise_for_status()
                segment_data = response.content
                # Decrypt if necessary (use playlist-provided key/iv/method)
                if current_key["METHOD"] == 'AES-128' and current_key.get("KEY"):
                    decrypted = decrypt_segment(segment_data, segment_sequence, None, current_key)
                    if decrypted is None:
                        logger.debug(f"🗑️ Skipping segment {segment_sequence}: decryption failed")
                        return None
                    segment_data = decrypted
                return segment_data
            except Exception as e:
                logger.debug(f"❌ Error downloading segment (attempt {attempt + 1}): {e}")
                attempt += 1
                time.sleep(1)  # Short delay before retry
        logger.debug(f"❌ Failed to download segment {segment_sequence} after {max_retries} attempts.")
        return None

    def append_to_ts(self, rec_file, segment_data, current_uri, segment_index):
        """Append segment data to the current file, with global timestamp normalization for all segments (including fillers)"""
        try:
            if not is_valid_ts_segment(segment_data):
                logger.debug(f"✗ Invalid TS segment data for {current_uri}")

            with open(rec_file, 'ab') as f:
                f.write(segment_data)
                f.flush()
            file_size = os.path.getsize(rec_file) / (1024 * 1024)
            uri_name = os.path.basename(current_uri)
            logger.debug("=" * 70)
            logger.debug(f"✓ Appended segment {segment_index}, {uri_name} to {rec_file} {file_size:.2f} MB")
            logger.debug("=" * 70)
            return True

        except Exception as e:
            logger.debug(f"❌ Error appending to output file: {e}")
            return False

    def record_stream(self, channel_uri, rec_file):
        """Main recording"""
        logger.debug("🎬 HLS Recorder with timestamp recalculation")
        logger.debug(f"channel_uri: {channel_uri}, rec_file: {rec_file}")

        segment_index = 0
        self.is_running = True
        endlist_recovery_triggered = False
        media_playlist_url = get_master_playlist(self.session, channel_uri)
        previous_uri = None
        previous_duration = 0
        previous_pts = 0
        pat_pmt_seen = False
        packet_index = -1

        # Track real playback time vs stream time
        continuous_pts = 0  # Continuous timeline for playback (never goes backwards)

        # Empty playlist handling
        empty_playlist_count = 0
        max_empty_playlists = 10

        # Failed playlist fetching
        failed_playlist_count = 0
        max_failed_playlists = 5

        cc_map = {}  # Map to track CC

        try:
            while not self._stop_event.is_set():

                playlist = get_playlist(self.session, media_playlist_url)
                if not playlist:
                    logger.debug("❌ Failed to fetch playlist, retrying...")
                    failed_playlist_count += 1
                    if failed_playlist_count >= max_failed_playlists:
                        logger.debug("❌ Too many failed playlist fetches. Aborting...")
                        self.server.broadcast({"command": "stop", "args": ["error", self.channel, rec_file]})
                        break
                    time.sleep(1)
                    continue

                uri_list = self.playlist_processor.process(playlist)

                # Recovery: If ENDLIST is seen, re-fetch master playlist and reset processor
                if self.playlist_processor.endlist_seen and not endlist_recovery_triggered:
                    logger.debug("🛑 ENDLIST detected in playlist. Triggering recovery: re-fetching master playlist and resetting playlist processor.")
                    media_playlist_url = get_master_playlist(self.session, channel_uri)
                    self.playlist_processor = HLSPlaylistProcessor()
                    endlist_recovery_triggered = True
                    time.sleep(1)
                    continue
                if not self.playlist_processor.endlist_seen:
                    endlist_recovery_triggered = False

                # Get encryption info from playlist processor
                encryption_info = self.playlist_processor.get_current_encryption_info()
                logger.debug(f" Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")

                # If no new segments, wait a bit and try again
                if not uri_list:
                    empty_playlist_count += 1
                    logger.debug("⏳ No new segments found, waiting for next playlist update...")
                    if empty_playlist_count >= max_empty_playlists:
                        logger.debug("❌ Playlist has been empty for too long. Aborting...")
                        self.server.broadcast({"command": "stop", "args": ["empty", self.channel, rec_file]})
                        break
                    time.sleep(0.5)
                    continue
                empty_playlist_count = 0  # Reset on success

                logger.debug(f"🔄 URI list has {len(uri_list)} new segments")

                for current_uri, segment_encryption_info, current_duration in uri_list:
                    logger.debug(f"Segment URI list 1: {segment_index}: {current_uri}")
                    logger.debug(f"Segment URI list 2: {segment_index}: {segment_encryption_info.get('METHOD', 'none')}")
                    logger.debug(f"Segment URI list 3: {segment_index}: {current_duration}")
                    if self._stop_event.is_set():
                        logger.debug("🛑 Recording stopped by user")
                        break
                    # Handle per-segment encryption info
                    current_key = get_encryption_info(self.session, segment_encryption_info, encryption_info)
                    segment_data = self.download_segment(current_uri, segment_index, current_key, max_retries=10, timeout=10)

                    if not segment_data or not is_valid_ts_segment(segment_data):
                        logger.debug(f"✗ Failed to download segment or invalid ts segment {segment_index}")
                        continue

                    # Wait for first pat + pmt to be available in stream
                    if not pat_pmt_seen and "_plutotv_error_" not in current_uri:
                        _pat_pkt, _pmt_pkt, packet_index = find_pat_pmt_in_segment(segment_data)
                        if packet_index == -1:
                            logger.debug(f"⚠ Segment {segment_index} does not have PAT/PMT, skipping")
                            continue
                    pat_pmt_seen = True

                    # Find video PID in segment
                    current_pts, _ = read_pts_from_segment(segment_data)
                    if current_pts is None:
                        raise ValueError(f"No PTS found in segment {segment_index}")

                    continuous_pts += previous_duration
                    offset = continuous_pts - current_pts
                    segment_data = shift_segment(segment_data, offset)
                    logger.debug(f"### Previous PTS: {previous_pts}, Previous duration: {previous_duration}, Current PTS: {current_pts}, Continuous PTS: {continuous_pts}, Offset: {offset}")

                    # update cc counters for this segment
                    segment_data, cc_map = update_continuity_counters(segment_data, cc_map)

                    # Check if segment URI has changed
                    if previous_uri and different_uris(previous_uri, current_uri):
                        logger.debug(f"Changing URI from: {previous_uri}")
                        logger.debug(f"Changing URI to:   {current_uri}")
                        segment_data = set_discontinuity_segment(segment_data, force=True)

                    # ready to append segment data to file
                    self.append_to_ts(rec_file, segment_data, current_uri, segment_index)

                    if segment_index == 3:
                        # send recording start message to client
                        if hasattr(self, 'server'):
                            self.server.broadcast({"command": "start", "args": [self.channel, rec_file]})

                    segment_index += 1
                    previous_uri = current_uri
                    previous_duration = current_duration
                    previous_pts = current_pts

        except KeyboardInterrupt:
            logger.debug("⚠ Recording interrupted by user")
            return True
        except Exception as e:
            logger.debug(f"❌ Recording error: {e}")
            self.server.broadcast({"command": "stop", "args": ["error", self.channel, rec_file]})
            traceback.print_exc()
            return False
        finally:
            self.is_running = False
            logger.debug("✓ Recording stopped")

    def start(self, channel, rec_file):
        """Start the recording process"""
        self._stop_event.clear()
        self.channel = channel
        channel_uri = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{channel}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="
        logger.debug(f"📺 Using channel URI: {channel_uri}")
        if self.is_running:
            self.stop()
        while self.is_running:
            logger.debug("⚠️ Recording is still running. waiting...")
            time.sleep(0.1)

        if os.path.exists(rec_file):
            os.remove(rec_file)
            # os.remove(rec_file + '.ap')  # Remove associated .ap file
            logger.debug(f"🧹 Removed old file: {rec_file}")

        threading.Thread(target=self.record_stream, args=(channel_uri, rec_file), daemon=True).start()

    def stop(self):
        """Stop the recording"""
        logger.debug("🛑 Stopping recording...")
        self._stop_event.set()


def main():
    """Main function to run the recorder"""
    logger.debug("🎬 HLS Recorder")

    parser = argparse.ArgumentParser(description="HLS Recorder")
    parser.add_argument('--rec_file', type=str, default='pluto.ts', help='File name for recording file (default: pluto.ts)')
    parser.add_argument('--channel', type=str, help='Channel ID')
    parser.add_argument('--server', action='store_true', help='Run in socket server mode')
    args = parser.parse_args()

    recorder = HLS_Recorder()
    server = None
    try:
        if args.server:
            # Start socket server in background thread
            HOST, PORT = "0.0.0.0", 5000
            server = RecorderSocketServer((HOST, PORT), RecorderCommandHandler, recorder)
            recorder.server = server
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            logger.debug(f"🔌 Command socket server running on {HOST}:{PORT}")
            logger.debug("🚀 Ready for commands. Use 'start', 'stop' via socket.")
            while True:
                time.sleep(1)
        elif args.channel:
            # Start recording immediately (no thread)
            logger.debug("Press Ctrl+C to exit.")
            recorder.start(args.channel, args.rec_file)
        else:
            logger.debug("Error: Must specify --channel or --server")
            sys.exit(1)
    except KeyboardInterrupt:
        logger.debug("\n⚠️ Interrupted by user")
        recorder.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
    except Exception as e:
        logger.debug(f"❌ Unexpected error: {e}")
        traceback.print_exc()
        recorder.stop()
        if server is not None:
            server.shutdown()
            server.server_close()

    logger.debug("🏁 Recording session ended")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
PlutoTV HLS Segment Downloader
Downloads, decrypts segments segment-by-segment and stores them in <rec_dir>/pluto.ts
"""

import os
import sys
import time
import json
import threading
import traceback
import argparse
import socketserver
import requests

from hls_playlist_utils import get_master_playlist, get_playlist, different_uris
from crypt_utils import decrypt_segment, get_encryption_info
from ts_utils import shift_segment, read_pts_from_segment, is_valid_ts_segment, set_discontinuity_segment  # , append_iframes_to_ap

from playlist import HLSPlaylistProcessor


class PlutoTVRecorder:

    def __init__(self):
        self.is_running = False
        self._stop_event = threading.Event()

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
                print(f"🔽 Downloading segment {segment_sequence}: {os.path.basename(segment_url)} (attempt {attempt + 1})")
                response = self.session.get(segment_url, timeout=timeout)
                response.raise_for_status()
                segment_data = response.content
                # Decrypt if necessary (use playlist-provided key/iv/method)
                if current_key["METHOD"] == 'AES-128' and current_key.get("KEY"):
                    decrypted = decrypt_segment(segment_data, segment_sequence, None, current_key)
                    if decrypted is None:
                        print(f"🗑️ Skipping segment {segment_sequence}: decryption failed")
                        return None
                    segment_data = decrypted
                return segment_data
            except Exception as e:
                print(f"❌ Error downloading segment (attempt {attempt + 1}): {e}")
                attempt += 1
                time.sleep(1)  # Short delay before retry
        print(f"❌ Failed to download segment {segment_sequence} after {max_retries} attempts.")
        return None

    def append_to_output(self, rec_file, segment_data, current_uri):
        """Append segment data to the current file, with global timestamp normalization for all segments (including fillers). Also update .ap file for Dreambox seeking."""
        try:
            if not is_valid_ts_segment(segment_data):
                print(f"✗ Invalid TS segment data for {current_uri}")

            # Get current file size before writing (offset for .ap)
            # offset = os.path.getsize(rec_file) if os.path.exists(rec_file) else 0
            with open(rec_file, 'ab') as f:
                f.write(segment_data)
            # Generate/update .ap file
            # ap_file = rec_file + '.ap'
            # append_iframes_to_ap(segment_data, ap_file, offset)
            file_size = os.path.getsize(rec_file) / (1024 * 1024)
            uri_name = os.path.basename(current_uri)
            print(f"✓ Appended segment {uri_name} to {rec_file} {file_size:.2f} MB")
            print("\n ==========================================\n")
            return True

        except Exception as e:
            print(f"❌ Error appending to output file: {e}")
            return False

    def record_stream(self, channel_uri, rec_file):
        """Main recording"""
        print("🎬 PlutoTV HLS Recorder with timestamp recalculation")
        print("=" * 70)
        print(f"Record file: {rec_file}")
        print("=" * 70)

        segment_index = 0
        self.is_running = True
        endlist_recovery_triggered = False
        media_playlist_url = get_master_playlist(self.session, channel_uri)
        previous_uri = None
        previous_duration = 0
        previous_pts = 0

        # Track real playback time vs stream time
        continuous_pts = 0  # Continuous timeline for playback (never goes backwards)

        try:
            while not self._stop_event.is_set():

                playlist = get_playlist(self.session, media_playlist_url)
                if not playlist:
                    print("❌ Failed to fetch playlist, retrying...")
                    time.sleep(1)
                    continue

                uri_list = self.playlist_processor.process(playlist)

                # Recovery: If ENDLIST is seen, re-fetch master playlist and reset processor
                if self.playlist_processor.endlist_seen and not endlist_recovery_triggered:
                    print("🛑 ENDLIST detected in playlist. Triggering recovery: re-fetching master playlist and resetting playlist processor.")
                    media_playlist_url = get_master_playlist(self.session, channel_uri)
                    self.playlist_processor = HLSPlaylistProcessor()
                    endlist_recovery_triggered = True
                    time.sleep(1)
                    continue
                if not self.playlist_processor.endlist_seen:
                    endlist_recovery_triggered = False

                # Get encryption info from playlist processor
                encryption_info = self.playlist_processor.get_current_encryption_info()

                print(f" Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")

                # If no new segments, wait a bit and try again
                if not uri_list:
                    print("⏳ No new segments found, waiting for next playlist update...")
                    time.sleep(0.5)
                    continue

                print(f"🔄 URI list has {len(uri_list)} new segments")

                for current_uri, segment_encryption_info, current_duration in uri_list:
                    print(f"Segment URI list 1: {segment_index}: {current_uri}")
                    print(f"Segment URI list 2: {segment_index}: {segment_encryption_info.get('METHOD', 'none')}")
                    print(f"Segment URI list 3: {segment_index}: {current_duration}")
                    # Handle per-segment encryption info
                    current_key = get_encryption_info(self.session, segment_encryption_info, encryption_info)
                    segment_data = self.download_segment(current_uri, segment_index, current_key, max_retries=10, timeout=10)

                    if not segment_data or not is_valid_ts_segment(segment_data):
                        print(f"✗ Failed to download segment or invalid ts segment {segment_index}")
                        continue

                    # wait for first PTS to be available in stream
                    first_pts, _ = read_pts_from_segment(segment_data)
                    print(f"Segment {segment_index} PTS: {first_pts}")
                    if first_pts is None:
                        print(f"⚠ Segment {segment_index} has no PTS, skipping")
                        continue

                    continuous_pts += previous_duration  # Update continuous timeline
                    offset = continuous_pts - first_pts
                    segment_data = shift_segment(segment_data, offset)
                    print(f"### Previous PTS: {previous_pts}, Previous duration: {previous_duration}, Current PTS: {first_pts}, Continuous PTS: {continuous_pts}, Offset: {offset}")

                    # Check if segment URI has changed
                    if previous_uri and different_uris(previous_uri, current_uri):
                        print(f"Changing segment URI from {previous_uri} to {current_uri}")
                        segment_data = set_discontinuity_segment(segment_data, force=True)

                    self.append_to_output(rec_file, segment_data, current_uri)

                    segment_index += 1
                    previous_uri = current_uri
                    previous_duration = current_duration
                    previous_pts = first_pts

            print("✓ Recording stopped")
            return True
        except KeyboardInterrupt:
            print("\n⚠ Recording interrupted by user")
            return True
        except Exception as e:
            print(f"❌ Recording error: {e}")
            traceback.print_exc()
            return False
        finally:
            self.is_running = False
            self._stop_event.clear()

    def start(self, channel, rec_file):
        """Start the recording process"""
        channel_uri = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{channel}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="
        print(f"📺 Using PlutoTV channel URI: {channel_uri}")
        if self.is_running:
            self.stop()
        while self.is_running:
            print("⚠️ Recording is still running. waiting...")
            wait_time = 0.1
            print(f"⏳ Waiting {wait_time} seconds before retrying...")
            time.sleep(wait_time)

        if os.path.exists(rec_file):
            os.remove(rec_file)
            # os.remove(rec_file + '.ap')  # Remove associated .ap file
            print(f"🧹 Removed old file: {rec_file}")

        threading.Thread(target=self.record_stream, args=(channel_uri, rec_file), daemon=True).start()

    def stop(self):
        """Stop the recording"""
        print("🛑 Stopping recording...")
        if self.is_running:
            self._stop_event.set()


# --- Socket server for command control ---
class RecorderCommandHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.data = self.request.recv(4096).strip().decode()
            req = json.loads(self.data)
            print(f"socket server received: {req}")
            cmd = req.get("command", "")
            if cmd == "start":
                # Pass both channel and rec_file as a tuple in args
                args = req.get("args", [])
                channel_uri = args[0]
                rec_file = args[1]
                self.server.recorder.start(channel_uri, rec_file)
            elif cmd == "stop":
                self.server.recorder.stop()
        except Exception as e:
            print(f"❌ Error handling command: {e}")


class RecorderSocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, recorder):
        super().__init__(server_address, handler_class)
        self.recorder = recorder


def main():
    """Main function to run the PlutoTV recorder"""
    print("🎬 PlutoTV HLS Recorder")

    parser = argparse.ArgumentParser(description="PlutoTV HLS Recorder")
    parser.add_argument('--rec_file', type=str, default='pluto.ts', help='File name for recording file (default: pluto.ts)')
    parser.add_argument('--channel', type=str, help='PlutoTV channel ID')
    parser.add_argument('--server', action='store_true', help='Run in socket server mode')
    args = parser.parse_args()

    recorder = PlutoTVRecorder()

    server = None
    try:
        if args.server:
            # Start socket server in background thread
            HOST, PORT = "0.0.0.0", 5000
            server = RecorderSocketServer((HOST, PORT), RecorderCommandHandler, recorder)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            print(f"🔌 Command socket server running on {HOST}:{PORT}")
            print("🚀 Ready for commands. Use 'start', 'stop' via socket.")
            while True:
                time.sleep(1)
        elif args.channel:
            # Start recording immediately (no thread)
            print("Press Ctrl+C to exit.")
            recorder.start(args.channel, args.rec_file)
        else:
            print("Error: Must specify --channel or --server")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        recorder.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        traceback.print_exc()
        recorder.stop()
        if server is not None:
            server.shutdown()
            server.server_close()

    print("🏁 Recording session ended")


if __name__ == "__main__":
    main()

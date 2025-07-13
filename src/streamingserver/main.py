#!/usr/bin/env python3
"""
PlutoTV HLS Segment Downloader
Downloads and decrypts segments segment-by-segment

Features:
- Real                  uri_list = self.playlist_processor.process(playlist)
                
                # Get encryption info from playlist processor (for fallback)
                encryption_info = self.playlist_processor.get_current_encryption_info()
                
                print(f"📋 Processed playlist has {len(uri_list)} segments")
                print(f"🔐 Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")             print(f"📋 Processed playlist has {len(uri_list)} segments")
                print(f"🔐 Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")             print(f"📋 Processed playlist has {len(uri_list)} segments")
                print(f"🔐 Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")             print(f"📋 Processed playlist has {len(uri_list)} segments")
                print(f"🔐 Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")   uri_list = self.playlist_processor.process(playlist)
                
                # Get encryption info from playlist processor
                encryption_info = self.playlist_processor.get_current_encryption_info()
                
                print(f"📋 Processed playlist has {len(uri_list)} segments")
                print(f"🔐 Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")LS segment downloading
- AES-128 decryption support
"""

import os
import re
import time
import threading
import traceback
import argparse
import requests

from ts_utils import is_valid_ts_segment
from hls_playlist_utils import get_master_playlist, is_filler, get_playlist
from crypt_utils import decrypt_segment, download_encryption_key, get_encryption_info
from ts_timestamp_utils import shift_segment, read_pts_from_segment, set_discontinuity_segment
from parse_hls import parse_attributes
from playlist import HLSPlaylistProcessor


class PlutoTVRecorder:

    def __init__(self, rec_file, hls_url):
        self.rec_file = rec_file
        self.hls_url = hls_url
        self.is_running = False
        self._stop_event = threading.Event()

        self.check_pts = 0
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

    def download_segment(self, segment_url, segment_sequence, current_key):
        """Download and decrypt a single HLS segment"""
        try:
            print(f"🔽 Downloading segment {segment_sequence}: {os.path.basename(segment_url)}")

            download_encryption_key(self.session, segment_url)

            response = self.session.get(segment_url, timeout=10)
            response.raise_for_status()

            segment_data = response.content
            # print(f"📥 Downloaded {len(segment_data)} bytes")

            # Decrypt if necessary (use playlist-provided key/iv/method, not self)
            if current_key["METHOD"] == 'AES-128' and current_key["KEY"]:
                decrypted = decrypt_segment(segment_data, segment_sequence, None, current_key["METHOD"], current_key["KEY"], current_key["IV"])
                if decrypted is None:
                    print(f"🗑️ Skipping segment {segment_sequence}: decryption failed")
                    return None
                segment_data = decrypted

            return segment_data

        except Exception as e:
            print(f"❌ Error downloading segment: {e}")
            return None

    def append_to_output(self, segment_data):
        """Append segment data to the current file, with global timestamp normalization for all segments (including fillers)."""
        pts = read_pts_from_segment(segment_data, first=True)
        if pts < self.check_pts:
            print(f">>>>>>>>>>>>>>>>> PTS DECREASING: current: {pts}, last: {self.check_pts}")
        self.check_pts = pts
        try:
            with open(self.rec_file, 'ab') as f:
                f.write(segment_data)
            file_size = os.path.getsize(self.rec_file) / (1024 * 1024)
            print(f"✓ Appended segment to {self.rec_file} {file_size:.2f} MB")
            print("\n ==========================================\n")
            return True

        except Exception as e:
            print(f"❌ Error appending to output file: {e}")
            return False


    def record_stream(self):
        """Main recording"""
        print("🎬 PlutoTV HLS Recorder with timestamp recalculation")
        print("=" * 70)
        print(f"Record file: {self.rec_file}")
        print("=" * 70)

        segment_index = 0
        self.is_running = True
        media_playlist_url = get_master_playlist(self.session, self.hls_url)
        last_pts = 0
        discontinuity_flag = True  # Start with discontinuity flag set

        try:
            while self.is_running and not self._stop_event.is_set():
                if self._stop_event.is_set():
                    break

                playlist = get_playlist(self.session, media_playlist_url)
                if not playlist:
                    print("❌ Failed to fetch playlist, retrying...")
                    time.sleep(1)
                    continue

                uri_list = self.playlist_processor.process(playlist)
                
                # Get encryption info from playlist processor
                encryption_info = self.playlist_processor.get_current_encryption_info()
                
                print(f"📋 Processed playlist has {len(playlist)} items")
                print(f"� Encryption info: METHOD={encryption_info.get('METHOD')}, URI={'present' if encryption_info.get('URI') else 'none'}, IV={'present' if encryption_info.get('IV') else 'none'}")
                for current_uri, segment_encryption_info in uri_list:
                    print(f"Segment URI list 1: {segment_index}: {current_uri}")
                    print(f"Segment URI list 2: {segment_index}: {segment_encryption_info.get('METHOD', 'none')}")
           
                    # Handle per-segment encryption info
                    current_key = get_encryption_info(self.session, segment_encryption_info, encryption_info)
                    segment_data = self.download_segment(current_uri, segment_index, current_key) 

                    if not segment_data or not is_valid_ts_segment(segment_data):
                        print(f"✗ Failed to download segment or invalid ts segment {segment_index}")
                        continue

                    # wait for first PTS to be available in stream
                    pts = read_pts_from_segment(segment_data, first=True)
                    print(f"Segment {segment_index} PTS: {pts}")
                    if discontinuity_flag:
                        if not pts:
                            print(f"⏳ Waiting for first PTS in segment {segment_index}")
                            continue
                        print("🔄 Discontinuity detected")
                    discontinuity_flag = False  # Reset after processing

                    first_pts = read_pts_from_segment(segment_data, first=True)
                    if first_pts:
                        offset = last_pts - first_pts
                        print(f"offset: {offset}")
                        segment_data = shift_segment(segment_data, offset)
                        new_first_pts = read_pts_from_segment(segment_data, first=True)
                        new_last_pts = read_pts_from_segment(segment_data, first=False)
                        print(f"Segment {segment_index} PTS: {first_pts} shifted PTS: {new_last_pts}, diff: {(new_last_pts - new_first_pts) / 90000}")
                        last_pts = new_last_pts
                    else:
                        # pts is None, cannot shift
                        print("⚠️ No valid PTS found in segment, cannot shift timestamps.")

                    # print(f"####### Segment {segment_index} PTS: {first_pts} shifted PTS: {last_pts}")

                    segment_index += 1
                    self.append_to_output(segment_data)
                    continue
            
                segment_index += 1
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

    def start(self):
        """Start the recording process"""
        return self.record_stream()

    def stop(self):
        """Stop the recording"""
        print("🛑 Stopping recording...")
        self.is_running = False
        self._stop_event.set()


def main():
    """Main function to run the PlutoTV recorder"""
    print("🎬 PlutoTV HLS Recorder")

    parser = argparse.ArgumentParser(description="PlutoTV HLS Recorder")
    parser.add_argument('--rec_dir', type=str, default=None, help='Recording directory (default: current directory)')
    parser.add_argument('--rec_file', type=str, default='pluto.ts', help='File name for recording file (default: pluto.ts)')
    parser.add_argument('--channel', type=str, default='62bc1784120ba80007935aaa', help='PlutoTV channel ID (default: 62bc1784120ba80007935aaa)')
    args = parser.parse_args()

    # Compose output_file_prefix with rec_dir if provided
    if args.rec_dir:
        rec_dir = args.rec_dir
        if not os.path.exists(rec_dir):
            os.makedirs(rec_dir, exist_ok=True)
        rec_file = os.path.join(rec_dir, args.rec_file)
    else:
        rec_file = args.rec_file

    # Erase old recording file before starting
    if os.path.exists(rec_file):
        os.remove(rec_file)
        print(f"🧹 Removed old file: {rec_file}")

    pluto_master_url = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{args.channel}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="

    print(f"📺 Using PlutoTV channel URL: {pluto_master_url}")
    recorder = PlutoTVRecorder(rec_file, pluto_master_url)

    try:
        # Start recording
        print("🚀 Starting recording...")
        print("Press Ctrl+C to stop recording")
        success = recorder.start()

        if success:
            print("✅ Recording completed successfully")
        else:
            print("❌ Recording failed")

    except KeyboardInterrupt:
        print("\n⚠️ Recording interrupted by user")
        recorder.stop()
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        traceback.print_exc()
        recorder.stop()

    print("🏁 Recording session ended")


if __name__ == "__main__":
    main()

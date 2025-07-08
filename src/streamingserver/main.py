#!/usr/bin/env python3
"""
PlutoTV HLS Segment Downloader
Downloads and decrypts segments segment-by-segment with automatic file splitting on discontinuities

Features:
- Real-time HLS segment downloading
- AES-128 decryption support
- Discontinuity detection (ads/content transitions)
"""

import os
import time
import re
import threading
import traceback
import glob
import argparse
from datetime import datetime
from urllib.parse import urljoin
import requests
import m3u8
from Crypto.Cipher import AES


class PlutoTVRecorder:
    def __init__(self, output_file_prefix):
        self.output_file_prefix = output_file_prefix
        self.is_running = False
        self._stop_event = threading.Event()

        # Encryption tracking
        self.current_key = None
        self.current_iv = None
        self.key_method = None

        self.current_file_number = 1
        self.current_output_file = None
        self.segments_in_current_file = 0
        # Balance minimum segments before allowing new file
        self.min_segments_per_file = 20

        # Enhanced discontinuity detection settings
        self.last_media_sequence = None
        self.last_discontinuity_sequence = None
        self.expected_segment_duration = 6.0  # Default HLS segment duration
        self.discontinuity_detected = False

        # Enhanced ad detection tracking
        self.segment_count = 0  # Total segments processed
        self.min_segments_before_split = 8  # More aggressive for ad detection

        # Option A+ tracking: count skipped vs valid segments
        self.valid_segments_count = 0  # Valid MPEG-TS segments written
        self.skipped_segments_count = 0  # Invalid segments skipped

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

        # PlutoTV channel HLS URL
        self.hls_url = "http://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/stitch/hls/channel/62bc1784120ba80007935aaa/master.m3u8?appName=web&appVersion=unknown&clientTime=0&deviceDNT=0&deviceId=84ac5692-4b92-11ef-aece-533610f1ea34&deviceMake=Chrome&deviceModel=web&deviceType=web&deviceVersion=unknown&includeExtendedEvents=false&serverSideAds=true&sid=c2c2d7cc-7d4d-4255-a842-24443e529840"

    def is_valid_ts_segment(self, segment_data):
        """Check if segment_data is a valid MPEG-TS segment (sync and video PID)."""
        if not segment_data or len(segment_data) < 188:
            return False, False
        sync_count = 0
        for i in range(0, min(len(segment_data), 188*5), 188):
            if i < len(segment_data) and segment_data[i] == 0x47:
                sync_count += 1
        has_valid_sync = sync_count >= 3
        video_pid_found = False
        for i in range(0, min(len(segment_data), 188*20), 188):
            pkt = segment_data[i:i+188]
            if len(pkt) < 188 or pkt[0] != 0x47:
                continue
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            if pid == 256 or (0x100 <= pid <= 0x1FF):
                video_pid_found = True
                break
        return has_valid_sync, video_pid_found

    def has_daterange_ad_marker(self, playlist_content):
        """
        Detect if #EXT-X-DATERANGE tags in the playlist mark an ad break for the current segment.
        Returns True if the segment is within an ad daterange, else False.
        """
        if not playlist_content:
            return False
        # Find all DATERANGE tags with CLASS="ad" or SCTE35-OUT/IN
        ad_ranges = []
        for m in re.finditer(r'#EXT-X-DATERANGE:([^\n]+)', playlist_content):
            attrs = m.group(1)
            if 'CLASS="ad"' in attrs or 'SCTE35-OUT' in attrs or 'SCTE35-IN' in attrs or 'SCTE-35-OUT' in attrs or 'SCTE-35-IN' in attrs:
                # Parse start and end times if present
                start = None
                end = None
                seg_match = re.search(r'START-DATE="([^"]+)"', attrs)
                if seg_match:
                    start = seg_match.group(1)
                end_match = re.search(r'END-DATE="([^"]+)"', attrs)
                if end_match:
                    end = end_match.group(1)
                ad_ranges.append((start, end))
        # If no ad dateranges, return False
        if not ad_ranges:
            return False
        # If segment_url is provided, try to match segment by EXTINF order
        # Otherwise, if any ad daterange is open (no END-DATE), treat as ad
        for start, end in ad_ranges:
            if end is None:
                return True
        return False

    def has_scte35_marker(self, segment_data):
        """
        Scan MPEG-TS segment for SCTE-35 cue messages (ad markers).
        Returns True if SCTE-35 splice_info_section is found, else False.
        """
        if not segment_data or len(segment_data) < 188:
            return False
        # Scan TS packets for SCTE-35 PID (commonly 0x0024 or 36)
        for i in range(0, len(segment_data) - 188 + 1, 188):
            pkt = segment_data[i:i+188]
            if pkt[0] != 0x47:
                continue
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            # SCTE-35 PID is often 0x0024 (36), but can vary
            if pid == 0x24 or pid == 0xFC8:  # 0xFC8 is also used sometimes
                # Check for pointer_field and table_id 0xFC (SCTE-35)
                adaptation_field_control = (pkt[3] >> 4) & 0x03
                # payload_start = 4
                if adaptation_field_control in (1, 3):
                    if pkt[1] & 0x40:  # payload_unit_start_indicator
                        pointer_field = pkt[4]
                        table_id = pkt[5 + pointer_field] if 5 + pointer_field < len(pkt) else None
                        if table_id == 0xFC:
                            return True
        return False

    def parse_encryption_info(self, playlist_content, playlist_url):
        """Parse encryption information from HLS playlist"""
        try:
            # Look for EXT-X-KEY tags
            key_pattern = r'#EXT-X-KEY:([^\r\n]+)'
            key_matches = re.findall(key_pattern, playlist_content)

            if key_matches:
                # Parse the most recent key (last one in playlist)
                key_line = key_matches[-1]
                print(f"🔐 Found encryption key line: {key_line}")

                # Parse key attributes
                attributes = {}
                for attr in key_line.split(','):
                    if '=' in attr:
                        key_attr, value = attr.split('=', 1)
                        # Remove quotes from value
                        value = value.strip('"\'')
                        attributes[key_attr.strip()] = value

                # Extract method, URI, and IV
                method = attributes.get('METHOD', '').upper()
                uri = attributes.get('URI', '')
                iv = attributes.get('IV', '')

                print(f"🔐 Encryption method: {method}")
                print(f"🔐 Key URI: {uri}")
                print(f"🔐 IV: {iv}")

                if method == 'AES-128' and uri:
                    # Download the encryption key
                    key_url = urljoin(playlist_url, uri) if not uri.startswith('http') else uri
                    encryption_key = self.download_encryption_key(key_url)

                    if encryption_key:
                        self.current_key = encryption_key
                        self.key_method = method

                        # Parse IV
                        if iv:
                            # Remove 0x prefix if present
                            if iv.startswith('0x') or iv.startswith('0X'):
                                iv = iv[2:]
                            # Convert hex string to bytes
                            try:
                                self.current_iv = bytes.fromhex(iv)
                                print(f"✓ Using provided IV: {iv}")
                            except ValueError:
                                print(f"⚠ Invalid IV format: {iv}")
                                self.current_iv = None
                        else:
                            # No IV provided, will use segment sequence number
                            self.current_iv = None
                            print("📝 No IV provided, will derive from segment sequence")

                        print("✓ Encryption key loaded successfully")
                        return True
                    else:
                        print("❌ Failed to download encryption key")
                        return False
                elif method == 'NONE':
                    print("✓ No encryption (METHOD=NONE)")
                    self.current_key = None
                    self.current_iv = None
                    self.key_method = None
                    return True
                else:
                    print(f"⚠ Unsupported encryption method: {method}")
                    return False
            else:
                print("📝 No encryption keys found in playlist")
                self.current_key = None
                self.current_iv = None
                self.key_method = None
                return True

        except Exception as e:
            print(f"❌ Error parsing encryption info: {e}")
            traceback.print_exc()
            return False

    def download_encryption_key(self, key_url):
        """Download the encryption key from the given URL"""
        try:
            print(f"🔑 Downloading encryption key from: {key_url}")
            response = self.session.get(key_url, timeout=10)
            response.raise_for_status()

            key_data = response.content
            print(f"🔑 Downloaded key: {len(key_data)} bytes")

            # AES-128 keys should be exactly 16 bytes
            if len(key_data) == 16:
                return key_data
            else:
                print(f"⚠ Unexpected key length: {len(key_data)} bytes (expected 16)")
                return key_data  # Return anyway, might still work

        except Exception as e:
            print(f"❌ Error downloading encryption key: {e}")
            return None

    def decrypt_segment(self, encrypted_data, segment_sequence=0, media_sequence_base=None):
        """Decrypt an encrypted HLS segment using AES-128 (no PKCS7 removal for TS)"""
        if not self.current_key or self.key_method != 'AES-128':
            # No encryption or unsupported method
            print("📝 No encryption key or unsupported method, returning encrypted data as is")
            return encrypted_data

        try:
            # Determine IV
            if self.current_iv:
                iv = self.current_iv
                iv_source = 'playlist'
            else:
                # Use EXT-X-MEDIA-SEQUENCE as base if provided
                seq = segment_sequence
                if media_sequence_base is not None:
                    seq = media_sequence_base + segment_sequence
                iv = seq.to_bytes(16, byteorder='big')
                iv_source = f'seq={seq}'

            print(f"🔓 Decrypting segment (segment_sequence: {segment_sequence}, IV source: {iv_source})")
            print(f"🔓 Key: {self.current_key.hex()} IV: {iv.hex()}")

            # Create AES cipher
            cipher = AES.new(self.current_key, AES.MODE_CBC, iv)
            decrypted_data = cipher.decrypt(encrypted_data)

            # Log first 32 bytes of decrypted data for debug
            print(f"[DECRYPT DEBUG] First 32 bytes: {decrypted_data[:32].hex()}")

            # Save first decrypted segment for offline analysis
            if segment_sequence == 1:
                try:
                    with open("decrypted_segment1.ts", "wb") as f:
                        f.write(decrypted_data)
                    print("[DECRYPT DEBUG] Saved first decrypted segment to decrypted_segment1.ts")
                except Exception as e:
                    print(f"[DECRYPT DEBUG] Failed to save decrypted segment: {e}")

            print(f"✓ Segment decrypted successfully ({len(decrypted_data)} bytes)")
            return decrypted_data

        except Exception as e:
            print(f"❌ Error decrypting segment: {e}")
            print(f"   Key: {self.current_key.hex() if self.current_key else None}")
            print(f"   IV: {self.current_iv.hex() if self.current_iv else 'derived'} (seq: {segment_sequence})")
            traceback.print_exc()
            # Do NOT return encrypted data, skip segment instead
            return None

    def get_master_playlist(self, url):
        """Get the master playlist URL and find the best quality stream"""
        try:
            print(f"🔍 Getting master playlist from: {url}")
            response = self.session.get(url, timeout=15)
            response.raise_for_status()

            # Parse the master playlist
            master_playlist = m3u8.loads(response.text)

            if master_playlist.playlists:
                # Sort by bandwidth and get the best quality
                sorted_playlists = sorted(
                    master_playlist.playlists,
                    key=lambda p: p.stream_info.bandwidth if p.stream_info else 0
                )

                # Get highest quality
                best_playlist = sorted_playlists[-1]
                media_url = urljoin(url, best_playlist.uri)

                bandwidth = best_playlist.stream_info.bandwidth if best_playlist.stream_info else 0
                resolution = best_playlist.stream_info.resolution if best_playlist.stream_info and best_playlist.stream_info.resolution else "unknown"

                print(f"✓ Selected stream: {bandwidth//1000}kbps, {resolution} resolution")
                print(f"✓ Media playlist URL: {media_url}")

                return media_url
            else:
                # Already a media playlist
                print(f"✓ Direct media playlist URL: {url}")
                return url

        except Exception as e:
            print(f"❌ Error getting master playlist: {e}")
            traceback.print_exc()
            return None

    def get_playlist_segments(self, playlist_url):
        """Get segments from a media playlist"""
        try:
            response = self.session.get(playlist_url, timeout=30)
            if response.status_code != 200:
                print(f"⚠ Failed to fetch playlist: HTTP {response.status_code}")
                return None

            print(f"📜 Fetched playlist ({len(response.text)} bytes)")
            # Store the latest playlist content for split/ad detection logic
            self.last_playlist_content = response.text

            # Check for discontinuities in the playlist
            self.detect_discontinuity_in_playlist(response.text)
            self.detect_media_sequence_jump(response.text)

            # Check for encryption keys in the playlist
            self.parse_encryption_info(response.text, playlist_url)

            # Parse the playlist
            playlist = m3u8.loads(response.text)

            if playlist.segments:
                segment_uris = [urljoin(playlist_url, segment.uri) for segment in playlist.segments]
                print(f"✓ Found {len(segment_uris)} segments in playlist")

                # Log discontinuity status
                if self.discontinuity_detected:
                    print("🔄 MAJOR discontinuity detected in playlist)")
                return segment_uris
            else:
                print("⚠ Playlist contains no segments")
                return []

        except Exception as e:
            print(f"❌ Error getting playlist segments: {e}")
            traceback.print_exc()
            return None

    def download_segment(self, segment_url, segment_sequence=0):
        """Download and decrypt a single HLS segment"""
        try:
            print(f"🔽 Downloading segment {segment_sequence}: {os.path.basename(segment_url)}")

            response = self.session.get(segment_url, timeout=10)
            response.raise_for_status()

            segment_data = response.content
            print(f"📥 Downloaded {len(segment_data)} bytes")

            # Decrypt if necessary
            if self.current_key and self.key_method == 'AES-128':
                decrypted = self.decrypt_segment(segment_data, segment_sequence)
                if decrypted is None:
                    print(f"🗑️ Skipping segment {segment_sequence}: decryption failed")
                    return None
                segment_data = decrypted

            return segment_data

        except Exception as e:
            print(f"❌ Error downloading segment: {e}")
            return None

    def should_start_new_file_for_segment(self, segment_data, segment_url=None):
        """
        Decide if a new file should be started for this segment.
        Split on:
        - EXT-X-DISCONTINUITY-SEQUENCE change (protocol-defined)
        - If segment is an ad (URL, SCTE-35, or DATERANGE) and previous segment was not an ad (ad block start)
        - If segment is not an ad and previous segment was an ad (content resumes)
        - If this segment is the first after a new #EXT-X-DISCONTINUITY tag (true segment-level discontinuity)
        - If transitioning from a known filler/bump segment to an ad segment, force a split (to avoid playback stutter)
        Each ad segment or ad block gets its own file, never appended to content.
        """
        # Track previous ad/filler state (do not update until after split logic)
        if not hasattr(self, '_last_segment_was_ad'):
            self._last_segment_was_ad = None
        if not hasattr(self, '_last_segment_was_filler'):
            self._last_segment_was_filler = None
        # Track previous segment base (creative/clip) for split-on-change
        if not hasattr(self, '_last_segment_base'):
            self._last_segment_base = None
        # --- Detect segment base (creative/clip) and check for change ---
        segment_base = None
        if segment_url:
            # Extract a base identifier for the segment (e.g., last two path components before .ts)
            # Example: .../clip/12345678_ad_bumper_00001.ts -> base: 12345678_ad_bumper
            m = re.search(r'/clip/([\w\-]+)', segment_url)
            if m:
                segment_base = m.group(1)
            else:
                # Fallback: use the filename without numeric suffix
                fname = os.path.basename(segment_url.split('?')[0])
                segment_base = re.sub(r'(_\d+)?\.ts$', '', fname)
        # Check for base change (clip/creative change)
        split_on_base_change = False
        if self._last_segment_base is not None and segment_base is not None:
            if segment_base != self._last_segment_base:
                split_on_base_change = True
                split_reason = f"segment base changed from {self._last_segment_base} to {segment_base} (clip/creative change)"
        playlist_content = getattr(self, 'last_playlist_content', None)
        # --- Detect ad and filler for current segment ---
        ad_url = self.is_ad_url(segment_url)
        scte35 = self.has_scte35_marker(segment_data)
        daterange = self.has_daterange_ad_marker(playlist_content)
        is_ad = ad_url or scte35 or daterange
        is_filler = False
        if segment_url:
            filler_patterns = [
                r'plutotv_error', r'error_clip', r'filler_content', r'technical_difficulties',
                r'stand_by', r'please_wait', r'_error_\d+_batch', r'error_\d+s?_',
                r'58e5371aad8e9c364d55f5d3_plutotv_error', r'Well_be_right_back', r'filler_\d+_batch',
                r'Space_Station_10s_Promo',
                r'/clip/6078029d33e416001a40d1c1_',
            ]
            for pat in filler_patterns:
                if re.search(pat, segment_url, re.IGNORECASE):
                    is_filler = True
                    break
        print(f"[AD DETECTION] Segment: {os.path.basename(segment_url) if segment_url else None} | ad_url={ad_url} | scte35={scte35} | daterange={daterange} | is_ad={is_ad}")
        print(f"[FILLER DETECTION] is_filler={is_filler}, _last_segment_was_filler={getattr(self, '_last_segment_was_filler', None)}")
        print(f"[SPLIT LOGIC] _last_segment_was_ad={self._last_segment_was_ad}, _last_segment_was_filler={self._last_segment_was_filler}, is_ad={is_ad}, is_filler={is_filler}")
        split_reason = None

        # --- True segment-level EXT-X-DISCONTINUITY detection ---
        # (Base change split takes priority)
        if split_on_base_change:
            # Update state before returning
            self._last_segment_base = segment_base
            self._last_segment_was_ad = is_ad
            self._last_segment_was_filler = is_filler
            return True, split_reason
        # We need to split only when this segment is the first after a new discontinuity tag.
        # We'll track the last discontinuity index seen, and compare to the current segment's index.
        if not hasattr(self, '_discontinuity_indices'):
            self._discontinuity_indices = set()
        if not hasattr(self, '_last_discontinuity_index'):
            self._last_discontinuity_index = None

        # Build a map of segment index to discontinuity for the current playlist
        # Only do this if playlist_content is available and segment_url is in the playlist
        if playlist_content and segment_url:
            # Find all segment URIs in playlist
            segment_pattern = r'(?:^|\n)([^#\n]+\.ts[^\n]*)'
            segment_uris = re.findall(segment_pattern, playlist_content, re.MULTILINE)
            print(f"[DEBUG] segment_url: {segment_url}")
            print(f"[DEBUG] segment_uris: {segment_uris}")
            # Find all discontinuity tag line numbers
            lines = playlist_content.splitlines()
            discontinuity_lines = set()
            for idx, line in enumerate(lines):
                if line.strip() == '#EXT-X-DISCONTINUITY':
                    discontinuity_lines.add(idx)
            # Map segment index to whether it is immediately after a discontinuity
            seg_idx = -1
            discontinuity_after = set()
            for idx, line in enumerate(lines):
                if line.startswith('#EXTINF:'):
                    seg_idx += 1
                if line.strip() == '#EXT-X-DISCONTINUITY' and seg_idx + 1 < len(segment_uris):
                    # The next segment after this tag is seg_idx+1
                    discontinuity_after.add(seg_idx + 1)
            print(f"[DEBUG] discontinuity_after: {discontinuity_after}")
            # Find the index of the current segment_url in the playlist (robust matching)
            seg_idx = None
            seg_url_base = os.path.basename(segment_url.split('?')[0]) if segment_url else None
            for i, uri in enumerate(segment_uris):
                uri_base = os.path.basename(uri.split('?')[0])
                print(f"[DEBUG] Matching uri: {uri} (base: {uri_base}) against segment_url: {segment_url} (base: {seg_url_base})")
                if uri == segment_url or uri in segment_url or segment_url.endswith(uri):
                    seg_idx = i
                    print(f"[DEBUG] Matched seg_idx (exact/in): {seg_idx}")
                    break
                elif seg_url_base and uri_base == seg_url_base:
                    seg_idx = i
                    print(f"[DEBUG] Matched seg_idx (basename): {seg_idx}")
                    break
            if seg_idx is None:
                print(f"[DEBUG] No match for segment_url in playlist segment_uris. segment_url: {segment_url}, segment_url_base: {seg_url_base}")
                print(f"[DEBUG] Playlist segment_uris: {segment_uris}")
                print(f"[DEBUG] Discontinuity after indices: {discontinuity_after}")
                # Fallback: if the previous segment was a discontinuity, force a split
                if len(discontinuity_after) > 0:
                    # If the last discontinuity index is not already used, force a split
                    last_disc = max(discontinuity_after)
                    if self._last_discontinuity_index != last_disc:
                        split_reason = "[FALLBACK] Forced split after EXT-X-DISCONTINUITY (no segment match)"
                        print(f"[DEBUG] SPLIT: {split_reason}")
                        self._last_discontinuity_index = last_disc
            print(f"[DEBUG] Final seg_idx: {seg_idx}")
            # If this segment is the first after a discontinuity, split
            if seg_idx is not None and seg_idx in discontinuity_after:
                if self._last_discontinuity_index != seg_idx:
                    split_reason = f"segment {seg_idx} is first after EXT-X-DISCONTINUITY"
                    print(f"[DEBUG] SPLIT: {split_reason}")
                    self._last_discontinuity_index = seg_idx
            else:
                # If not splitting, clear last discontinuity index if not on a discontinuity
                if seg_idx is not None and self._last_discontinuity_index == seg_idx:
                    self._last_discontinuity_index = None

        # --- Ad/content/filler transition split (including first segment logic) ---
        if self._last_segment_was_ad is None:
            # First segment: always split to ensure correct file for ads or content
            if is_ad:
                split_reason = "first segment is ad (start new file)"
            else:
                split_reason = "first segment is content (start new file)"
        elif is_ad != self._last_segment_was_ad:
            if is_ad:
                split_reason = "ad block started (URL, SCTE-35, or DATERANGE)"
            else:
                split_reason = "content resumed after ad"
        # FIX: Always check for filler->ad transition, regardless of ad/content transition
        if (self._last_segment_was_filler and not is_filler and is_ad and not self._last_segment_was_ad):
            split_reason = "forced split: transitioning from filler/bump to ad (to avoid playback stutter)"
            print(f"[SPLIT LOGIC] Forcing split due to filler->ad transition: _last_segment_was_filler={self._last_segment_was_filler}, is_filler={is_filler}, is_ad={is_ad}, _last_segment_was_ad={self._last_segment_was_ad}")
        # Update state only after all split logic
        self._last_segment_was_ad = is_ad
        self._last_segment_was_filler = is_filler
        self._last_segment_base = segment_base
        if split_reason:
            return True, split_reason
        return False, None

    def is_ad_url(self, segment_url):
        """Robust ad/filler segment detection by URL pattern."""
        if not segment_url:
            return False
        ad_patterns = [
            r'/ad/', r'/ads/', r'ad_bumper', r'plutotv_ad', r'bump', r'promo', r'filler', r'slate', r'break',
            r'interstitial', r'commercial', r'sponsor', r'preroll', r'midroll', r'postroll',
            r'ad_', r'_ad', r'ad-', r'-ad', r'adsegment', r'adsequence', r'adgroup', r'adbreak', r'admarker',
            r'adtag', r'adunit', r'admanager', r'adrequest', r'adresponse', r'adclick', r'adview', r'adtracking',
            r'adserver', r'adid', r'adurl', r'adtype', r'adcategory', r'adlabel', r'adslot', r'adzone', r'adblock',
            r'adpod', r'adtime', r'adstart', r'adend', r'admid', r'adcontent', r'adcreative', r'adasset', r'admedia',
            r'adfile', r'adpath', r'adsource', r'admeta', r'adinfo', r'adparams', r'adparam', r'adoption', r'adoptionid',
            r'adunitid', r'adunits', r'adgroupid', r'adgroups', r'adbreakid', r'adbreaks', r'admarkerid', r'admarkers',
            r'adtagid', r'adtags', r'adunitname', r'adunitpath', r'aduniturl', r'adunitmeta', r'adunitinfo',
            r'adunitparams', r'adunitparam', r'adunitadoption', r'adunitadoptionid', r'adunitgroupid', r'adunitgroups',
            r'adunitbreakid', r'adunitbreaks', r'adunitmarkerid', r'adunitmarkers', r'adunittagid', r'adunittags',
            r'adunitnameid', r'adunitnamepath', r'adunitnameurl', r'adunitnamemeta', r'adunitnameinfo',
            r'adunitnameparams', r'adunitnameparam', r'adunitnameadoption', r'adunitnameadoptionid', r'adunitnamegroupid',
            r'adunitnamegroups', r'adunitnamebreakid', r'adunitnamebreaks', r'adunitnamemarkerid', r'adunitnamemarkers',
            r'adunitnametagid', r'adunitnametags',
            r'doubleclick\\.net', r'googlesyndication', r'commercial_break', r'sponsor_message', r'_advertisement_', r'/advertisement/'
        ]
        for pattern in ad_patterns:
            if re.search(pattern, segment_url, re.IGNORECASE):
                return True
        return False

    def append_to_output(self, segment_data, segment_url=None):
        """Append segment data to the current file, skipping known error/filler segments."""
        try:
            # Detect error/filler by URL or content
            is_filler = False
            if segment_url:
                filler_patterns = [
                    r'plutotv_error', r'error_clip', r'filler_content', r'technical_difficulties',
                    r'stand_by', r'please_wait', r'_error_\d+_batch', r'error_\d+s?_',
                    r'58e5371aad8e9c364d55f5d3_plutotv_error', r'Well_be_right_back', r'filler_\d+_batch',
                    r'Space_Station_10s_Promo',
                    r'/clip/6078029d33e416001a40d1c1_',
                ]
                for pat in filler_patterns:
                    if re.search(pat, segment_url, re.IGNORECASE):
                        is_filler = True
                        print(f"[FILLER DETECTION] Skipping segment due to pattern: {pat}")
                        break

            # Minimal TS check: require sync and video PID
            has_valid_sync, video_pid_found = self.is_valid_ts_segment(segment_data)

            if is_filler:
                print(f"❌ Skipped segment #{self.segment_count}: detected as error/filler by URL")
                self.segment_count += 1
                return False

            if has_valid_sync and video_pid_found:
                current_file = self.get_current_output_file()
                with open(current_file, 'ab') as f:
                    f.write(segment_data)
                self.segments_in_current_file += 1
                self.segment_count += 1
                file_size = os.path.getsize(current_file) / (1024 * 1024)
                print(f"✓ Appended segment to {current_file} ({file_size:.2f} MB, {self.segments_in_current_file} segments, total: {self.segment_count})")
                return True
            else:
                print(f"❌ Skipped segment #{self.segment_count}: has_valid_sync={has_valid_sync}, video_pid_found={video_pid_found}")
                self.segment_count += 1
                return False
        except Exception as e:
            print(f"❌ Error appending to output file: {e}")
            return False

    def get_current_output_file(self):
        """Get the current output filename"""
        if not self.current_output_file:
            self.current_output_file = f"{self.output_file_prefix}_{self.current_file_number:05d}.ts"
        return self.current_output_file

    def start_new_file(self, reason="discontinuity detected"):
        """Start a new output file and reset parsing state (first file is always 00001).
        If the previous file is empty, do not increment file number and remove the empty file.
        If a file number is reused after an empty file, log this explicitly."""
        file_number_reused = False
        if self.current_output_file is None:
            # First file: do not increment file number
            self.current_output_file = f"{self.output_file_prefix}_{self.current_file_number:05d}.ts"
            print(f"🆕 (First file) Using: {self.current_output_file} (Reason: {reason})")
        else:
            # Check if previous file is empty (no valid segments appended)
            file_existed = os.path.exists(self.current_output_file)
            file_size = os.path.getsize(self.current_output_file) if file_existed else 0
            if self.segments_in_current_file == 0:
                if file_existed and file_size == 0:
                    # Remove empty file and do not increment file number
                    try:
                        os.remove(self.current_output_file)
                        print(f"🗑️ Removed empty file: {self.current_output_file} (no valid segments)")
                        file_number_reused = True
                    except Exception as e:
                        print(f"⚠️ Could not remove empty file: {self.current_output_file}: {e}")
                    # Do not increment file number, reuse the same number
                else:
                    # File was never created, do not increment file number
                    print(f"[INFO] No file was created for index {self.current_file_number:05d}, reusing file number.")
                # In both cases, do not increment file number
                # Set current_output_file to the same file number (do not increment)
                self.current_output_file = f"{self.output_file_prefix}_{self.current_file_number:05d}.ts"
                if file_number_reused:
                    print(f"[INFO] File number {self.current_file_number:05d} is being reused after previous empty file was deleted.")
                print(f"🆕 Starting new file: {self.current_output_file} (Reason: {reason})")
            else:
                if file_existed:
                    print(f"📁 Finished {self.current_output_file} ({file_size / (1024 * 1024):.2f} MB, {self.segments_in_current_file} segments)")
                self.current_file_number += 1
                self.current_output_file = f"{self.output_file_prefix}_{self.current_file_number:05d}.ts"
                print(f"🆕 Starting new file: {self.current_output_file} (Reason: {reason})")
        self.segments_in_current_file = 0
        self.discontinuity_detected = False
        # CRITICAL: Reset all parsing state for the new stream
        self.last_media_sequence = None  # Reset sequence tracking
        # Reset discontinuity sequence tracking
        self.last_discontinuity_sequence = None
        print("🔄 Reset parsing state for new stream detection")
        return self.current_output_file

    def detect_discontinuity_in_playlist(self, playlist_content):
        """
        Discontinuity detection: only protocol-defined events (no ad marker calls).
        This version maps #EXT-X-DISCONTINUITY tags to segment indices for robust, segment-level detection.
        """
        try:
            print("\n🔍 Discontinuity detection (segment-level, robust)...")

            # Track discontinuity sequence
            discontinuity_seq_pattern = r'#EXT-X-DISCONTINUITY-SEQUENCE:(\d+)'
            discontinuity_seq_match = re.search(discontinuity_seq_pattern, playlist_content)
            current_discontinuity_sequence = None
            if discontinuity_seq_match:
                current_discontinuity_sequence = int(discontinuity_seq_match.group(1))
            if (self.last_discontinuity_sequence is not None and
                current_discontinuity_sequence is not None and
                    current_discontinuity_sequence != self.last_discontinuity_sequence):
                print(f"🔄 MAJOR discontinuity sequence changed: {self.last_discontinuity_sequence} -> {current_discontinuity_sequence}")
                self.discontinuity_detected = True
                print("🎬 File split: Discontinuity sequence changed.")
                self.last_discontinuity_sequence = current_discontinuity_sequence
                return True
            self.last_discontinuity_sequence = current_discontinuity_sequence

            # Map #EXT-X-DISCONTINUITY tags to segment indices
            lines = playlist_content.splitlines()
            discontinuity_indices = []
            seg_idx = -1
            for line in lines:
                if line.startswith('#EXTINF'):
                    seg_idx += 1
                if line.startswith('#EXT-X-DISCONTINUITY'):
                    # The next segment (seg_idx+1) is the first after discontinuity
                    discontinuity_indices.append(seg_idx + 1)
            if discontinuity_indices:
                print(f"[DEBUG] Discontinuity indices (first segment after tag): {discontinuity_indices}")
            # Store for use in should_start_new_file_for_segment if needed
            self._discontinuity_indices = discontinuity_indices

            return False
        except Exception as e:
            print(f"⚠ Error detecting discontinuity: {e}")
            return False

    def detect_media_sequence_jump(self, playlist_content):
        """Detect major jumps in media sequence that indicate significant discontinuity"""
        try:
            # Look for EXT-X-MEDIA-SEQUENCE
            media_seq_pattern = r'#EXT-X-MEDIA-SEQUENCE:(\d+)'
            media_seq_match = re.search(media_seq_pattern, playlist_content)

            if media_seq_match:
                current_media_sequence = int(media_seq_match.group(1))

                if self.last_media_sequence is not None:
                    # Normal increment should be small
                    sequence_diff = current_media_sequence - self.last_media_sequence

                    # Log sequence progression for debugging
                    print(f"📊 Sequence check: prev={self.last_media_sequence}, curr={current_media_sequence}, diff={sequence_diff}")

                    # Only trigger on LARGE jumps (major content changes, not normal progression)
                    if sequence_diff > 150:  # Balanced threshold - between 100 and 200
                        print(f"📈 MAJOR media sequence jump detected: {self.last_media_sequence} -> {current_media_sequence} (diff: {sequence_diff})")
                        self.discontinuity_detected = True
                        return True
                    elif sequence_diff < -40:  # Balanced threshold for backwards jumps
                        print(f"⏪ MAJOR media sequence backwards jump: {self.last_media_sequence} -> {current_media_sequence}")
                        self.discontinuity_detected = True
                        return True
                else:
                    print(f"📊 First sequence in stream: {current_media_sequence}")

                self.last_media_sequence = current_media_sequence
                return False

        except Exception as e:
            print(f"⚠ Error checking media sequence: {e}")
            return False

        return False

    def record_stream(self):
        """Main recording loop with discontinuity detection (refactored for clarity)"""
        print("🎬 PlutoTV HLS Recorder with Discontinuity Detection")
        print("=" * 70)
        print(f"Output file prefix: {self.output_file_prefix}")
        print("Mode: Multi-file recording across discontinuities (ads/content transitions)")
        print("=" * 70)

        self.current_file_number = 1
        self._reset_recording_state()

        media_playlist_url = self.get_master_playlist(self.hls_url)
        if not media_playlist_url:
            print("❌ Could not find working playlist URL")
            return False

        segment_index = 0
        seen_segments = set()
        consecutive_failures = 0
        max_consecutive_failures = 10
        self.is_running = True

        try:
            while self.is_running and not self._stop_event.is_set():
                segments = self.get_playlist_segments(media_playlist_url)
                if not segments:
                    consecutive_failures += 1
                    print("⚠ No segments found, retrying in 5 seconds...")
                    if consecutive_failures >= max_consecutive_failures:
                        print("❌ Too many consecutive failures, stopping")
                        break
                    time.sleep(5)
                    continue
                consecutive_failures = 0

                new_segments = self._get_new_segments(segments, seen_segments)
                self._mark_segments_seen(segments, seen_segments)

                current_playlist_content = getattr(self, 'last_playlist_content', '')
                is_error_content = self.detect_error_filler_content(current_playlist_content)
                print(f"[DEBUG] is_error_content: {is_error_content}")

                if not new_segments and segments:
                    new_segments = self._handle_segment_pattern_change(segments, seen_segments)

                for segment_url in new_segments:
                    if self._stop_event.is_set():
                        break
                    segment_index += 1
                    if segment_index == 1:
                        print(f"�️ Skipping first segment {os.path.basename(segment_url)} (index 1) to avoid TS misalignment")
                        continue
                    segment_data = self.download_segment(segment_url, segment_index)
                    # Always call split logic, even for filler/invalid segments
                    start_new, reason = self.should_start_new_file_for_segment(segment_data, segment_url)
                    if start_new:
                        self.start_new_file(reason)
                    # Only append if not filler/invalid
                    appended = False
                    if segment_data:
                        appended = self.append_to_output(segment_data, segment_url)
                        if appended:
                            print(f"✓ Segment {segment_index} processed successfully")
                        else:
                            print(f"✗ Failed to append segment {segment_index}")
                    else:
                        print(f"✗ Failed to download segment {segment_index}")

                time.sleep(1)  # Short sleep to avoid busy loop
                self._show_progress(segment_index)

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

    def _reset_recording_state(self):
        self.current_output_file = None
        self.segments_in_current_file = 0
        self.segment_count = 0
        self.valid_segments_count = 0
        self.skipped_segments_count = 0
        self.last_media_sequence = None
        self.last_discontinuity_sequence = None
        self.discontinuity_detected = False

    def _get_new_segments(self, segments, seen_segments):
        return [seg for seg in segments if seg not in seen_segments]

    def _mark_segments_seen(self, segments, seen_segments):
        for segment_url in segments:
            if segment_url not in seen_segments:
                seen_segments.add(segment_url)

    def _handle_segment_pattern_change(self, segments, seen_segments):
        recent_seen = list(seen_segments)[-10:] if seen_segments else []
        current_basenames = [os.path.basename(seg) for seg in segments]
        recent_basenames = [os.path.basename(seg) for seg in recent_seen]
        if recent_basenames and current_basenames:
            current_pattern = set(re.sub(r'\d+', 'X', name) for name in current_basenames)
            recent_pattern = set(re.sub(r'\d+', 'X', name) for name in recent_basenames)
            if not current_pattern.intersection(recent_pattern):
                print("🔄 Segment pattern changed dramatically, clearing segment cache")
                print(f"   Previous pattern: {recent_pattern}")
                print(f"   Current pattern: {current_pattern}")
                seen_segments.clear()
                for segment_url in segments:
                    seen_segments.add(segment_url)
                return segments
        return []

    def _show_progress(self, segment_index):
        if segment_index % 10 == 0:
            current_file = self.get_current_output_file()
            if os.path.exists(current_file):
                file_size = os.path.getsize(current_file) / (1024 * 1024)
                total_files = self.current_file_number
                print(f"📊 Progress: {segment_index} total segments, {total_files} files, current file: {file_size:.2f} MB")

    def start(self):
        """Start the recording process"""
        return self.record_stream()

    def stop(self):
        """Stop the recording"""
        print("🛑 Stopping recording...")
        self.is_running = False
        self._stop_event.set()

        # Show final file summary
        if self.current_output_file and os.path.exists(self.current_output_file):
            file_size = os.path.getsize(self.current_output_file) / (1024 * 1024)
            print(f"📁 Final file: {self.current_output_file} ({file_size:.2f} MB, {self.segments_in_current_file} segments)")

        # Show segment processing statistics
        if self.segment_count > 0:
            print("\n📊 SEGMENT PROCESSING SUMMARY:")
            print(f"   ✅ Valid content segments written: {self.valid_segments_count}")
            print(f"   📺 Ad/filler segments saved separately: {self.skipped_segments_count}")
            print(f"   📝 Total segments processed: {self.segment_count}")

            if self.skipped_segments_count > 0:
                skip_percentage = (self.skipped_segments_count / self.segment_count) * 100
                print(f"   📈 {skip_percentage:.1f}% were ads/filler/corrupted")
                print("   🎯 Result: Clean content files + separate ad files (if playable)")

        # Show all created files
        all_files = sorted(glob.glob(f"{self.output_file_prefix}_[0-9]*.ts"))
        if all_files:
            total_size = sum(os.path.getsize(f) for f in all_files if os.path.exists(f)) / (1024 * 1024)
            print(f"\n📋 Created {len(all_files)} files, total size: {total_size:.2f} MB")

            if all_files:
                print("   🎬 CONTENT FILES (playable):")
                for file in all_files:
                    if os.path.exists(file):
                        size = os.path.getsize(file) / (1024 * 1024)
                        print(f"      📄 {file} ({size:.2f} MB)")

    def detect_error_filler_content(self, playlist_text: str) -> bool:
        """
        Detect if playlist contains error or filler content that should be separated.
        Enhanced to catch PlutoTV specific patterns.

        Returns:
            bool: True if error/filler content is detected
        """
        error_patterns = [
            # Original patterns
            r'plutotv_error',
            r'error_clip',
            r'filler_content',
            r'technical_difficulties',
            r'stand_by',
            r'please_wait',
            r'_error_\d+_batch',  # Common PlutoTV error pattern
            r'error_\d+s?_',      # Error with duration
            r'58e5371aad8e9c364d55f5d3_plutotv_error',  # Specific PlutoTV error ID
        ]

        # Check each pattern
        for pattern in error_patterns:
            if re.search(pattern, playlist_text, re.IGNORECASE):
                print(f"🔍 Error/filler content pattern detected: {pattern}")
                return True

        # Additional check: if playlist contains too many segment naming changes
        # This often indicates transitional/filler content
        segment_pattern = r'(?:^|\n)([^#\n]+\.ts[^\n]*)'
        segment_uris = re.findall(segment_pattern, playlist_text, re.MULTILINE)

        if len(segment_uris) >= 3:
            # Check for dramatic changes in segment naming that indicate filler
            unique_patterns = set()
            for uri in segment_uris:
                # Extract pattern without numbers
                base_pattern = re.sub(r'\d+', 'X', uri.split('/')[-1])
                base_pattern = re.sub(r'\.ts.*$', '.ts', base_pattern)
                unique_patterns.add(base_pattern)

            # If we have many different patterns, it might be transitional content
            if len(unique_patterns) > 2:
                print(f"🔍 Multiple segment patterns detected: {unique_patterns}")
                return True

        return False


def main():
    """Main function to run the PlutoTV recorder"""
    print("🎬 PlutoTV HLS Recorder")

    parser = argparse.ArgumentParser(description="PlutoTV HLS Recorder")
    parser.add_argument('--rec_dir', type=str, default=None, help='Recording directory (default: current directory)')
    parser.add_argument('--prefix', type=str, default='pluto', help='Base name for output files (default: pluto)')
    args = parser.parse_args()

    # Compose output_file_prefix with rec_dir if provided
    if args.rec_dir:
        rec_dir = args.rec_dir
        if not os.path.exists(rec_dir):
            os.makedirs(rec_dir, exist_ok=True)
        output_file_prefix = os.path.join(rec_dir, args.prefix)
        ts_pattern = os.path.join(rec_dir, f"{args.prefix}_*.ts")
    else:
        output_file_prefix = args.prefix
        ts_pattern = f"{args.prefix}_*.ts"

    # Erase old .ts files before starting
    for f in glob.glob(ts_pattern):
        try:
            os.remove(f)
            print(f"🧹 Removed old file: {f}")
        except Exception as e:
            print(f"⚠️ Could not remove {f}: {e}")

    recorder = PlutoTVRecorder(output_file_prefix=output_file_prefix)

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

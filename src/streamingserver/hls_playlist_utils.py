import os
import re
import traceback
import requests
from urllib.parse import urljoin
import m3u8
from crypt_utils import download_encryption_key


def get_master_playlist(session, url):
    """Get the master playlist URL and find the best quality stream"""
    try:
        print(f"🔍 Getting master playlist from: {url}")
        response = session.get(url, timeout=15)
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

            print(f"✓ Selected stream: {bandwidth // 1000}kbps, {resolution} resolution")
            print(f"✓ Media playlist URL: {media_url}")

            return media_url
        # Already a media playlist
        print(f"✓ Direct media playlist URL: {url}")
        return url

    except Exception as e:
        print(f"❌ Error getting master playlist: {e}")
        traceback.print_exc()
        return None


def get_playlist(session, playlist_url):
    """Get segments from a media playlist"""
    try:
        response = session.get(playlist_url, timeout=30)
        if response.status_code != 200:
            print(f"⚠ Failed to fetch playlist: HTTP {response.status_code}")
            return None
        print(f"📜 Fetched playlist ({len(response.text)} bytes)")
    except Exception as e:
        print(f"❌ Error fetching playlist: {e}")
        traceback.print_exc()
        return None
    return response.text


def get_playlist_segments(playlist_url, playlist):

        # Check for encryption keys in the playlist
        # self.key_method, self.current_key, self.current_iv = parse_encryption_info(self.last_playlist_content, playlist_url)

    try:
        # Parse the playlist
        playlist_obj = m3u8.loads(playlist)

        if playlist_obj.segments:
            # Return list of (segment_url, extinf_length)
            segment_info = [
                (urljoin(playlist_url, segment.uri), segment.duration)
                for segment in playlist_obj.segments
            ]
            print(f"✓ Found {len(segment_info)} segments in playlist")
            return segment_info
        print("⚠ Playlist contains no segments")
        return []
    except Exception as e:
        print(f"❌ Error getting playlist segments: {e}")
        traceback.print_exc()
        return None


def parse_encryption_info(playlist_content, playlist_url):
    """Parse encryption information from HLS playlist"""

    current_key = None
    current_iv = None
    key_method = None

    try:
        # Look for EXT-X-KEY tags
        key_pattern = r'#EXT-X-KEY:([^\r\n]+)'
        key_matches = re.findall(key_pattern, playlist_content)

        if key_matches:
            # Parse the most recent key (last one in playlist)
            key_line = key_matches[-1]
            # print(f"🔐 Found encryption key line: {key_line}")

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

            # print(f"🔐 Encryption method: {method}")
            # print(f"🔐 Key URI: {uri}")
            # print(f"🔐 IV: {iv}")

            if method == 'AES-128' and uri:
                # Download the encryption key
                key_url = urljoin(playlist_url, uri) if not uri.startswith('http') else uri
                # download_encryption_key requires a session and key_url
                # Use requests.Session() for this utility, or pass session as argument in future refactor
                session = requests.Session()
                encryption_key = download_encryption_key(session, key_url)

                if encryption_key:
                    current_key = encryption_key
                    key_method = method

                    # Parse IV
                    if iv:
                        # Remove 0x prefix if present
                        if iv.startswith('0x') or iv.startswith('0X'):
                            iv = iv[2:]
                        # Convert hex string to bytes
                        try:
                            current_iv = bytes.fromhex(iv)
                            # print(f"✓ Using provided IV: {iv}")
                        except ValueError:
                            print(f"⚠ Invalid IV format: {iv}")
                            current_iv = None
                    else:
                        # No IV provided, will use segment sequence number
                        current_iv = None
                        print("📝 No IV provided, will derive from segment sequence")

                    # print("✓ Encryption key loaded successfully")
                    return key_method, current_key, current_iv
                else:
                    print("❌ Failed to download encryption key")
                return key_method, current_key, current_iv
            else:
                print(f"⚠ Unsupported encryption method: {method}")
        else:
            print("📝 No encryption keys found in playlist")
        return key_method, current_key, current_iv

    except Exception as e:
        print(f"❌ Error parsing encryption info: {e}")
        traceback.print_exc()
        return key_method, current_key, current_iv


def is_filler(segment_url):
    """
    Detect if a segment is a filler based on URL and known filler patterns.
    Returns is_filler
    """

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
    print(f"[FILLER DETECTION] is_filler={is_filler}")
    return is_filler

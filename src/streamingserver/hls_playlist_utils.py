import re
from urllib.parse import urljoin
from urllib.parse import urlparse
import m3u8


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
        return None
    return response.text


def parse_attributes(attr_str):
    """
    Parses attribute string of form: key1=value1,key2="value2",...

    Returns dict of key:value pairs with quotes stripped.
    """
    attrs = {}
    pattern = re.compile(r'''([A-Z0-9\-]+)=(".*?"|[^",]*)''')
    for match in pattern.finditer(attr_str):
        key, val = match.groups()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        attrs[key] = val
    return attrs


def different_uris(uri1, uri2):
    """
    Compare two URIs and return True if they are different.
    URIs are different if hosts are different OR if hosts are same but first directory components are different.
    """
    if not uri1 or not uri2:
        return True  # If either is None, consider them different

    # Parse both URIs
    parsed1 = urlparse(uri1)
    parsed2 = urlparse(uri2)

    # Extract host (netloc)
    host1 = parsed1.netloc
    host2 = parsed2.netloc

    # Get first directory component from path
    path_parts1 = parsed1.path.strip('/').split('/')
    path_parts2 = parsed2.path.strip('/').split('/')
    first_dir1 = path_parts1[0] if path_parts1 and path_parts1[0] else ''
    first_dir2 = path_parts2[0] if path_parts2 and path_parts2[0] else ''

    # Different if hosts are different OR if hosts are same but first directory components are different
    return host1 != host2 or (host1 == host2 and first_dir1 != first_dir2)

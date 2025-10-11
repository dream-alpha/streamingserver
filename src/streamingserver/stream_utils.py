from urllib.parse import urlparse
import gzip
import requests
from debug import get_logger

logger = get_logger(__file__)


def detect_stream_type(url):
    logger.info(f"Detecting stream type for URL: {url}")
    try:
        # --- 0. Check for known M4S providers by URL pattern ---
        # Check for xHamster M4S streams (both hostname and IP-based URLs)
        if (".mp4.m3u8" in url
            and ("xhcdn.com" in url
                 or any(ip in url for ip in ("89.222.125.203", "79.127.216."))
                 or "/media=hls4/" in url)):
            logger.info("DEBUG: Detected xHamster M4S HLS stream by URL pattern")
            return "HLS_M4S"

        # --- 1. Check extension first ---
        path = urlparse(url).path.lower()
        if path.endswith(".m3u8"):
            # For M3U8 files, we need to check content to determine if it's M4S-based
            logger.info("DEBUG: M3U8 URL detected, will check content for M4S...")
            # Don't return here - continue to content inspection
        elif path.endswith(".mpd"):
            return "DASH"
        if path.endswith(".mp4"):
            return "MP4"
        if path.endswith(".webm"):
            return "WebM"
        if path.endswith(".ts"):
            return "TS segment"

        # --- 2. If no extension, check Content-Type header ---
        try:
            # Add User-Agent for YouTube CDN URLs
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            resp = requests.head(url, allow_redirects=True, timeout=10, headers=headers)
            content_type = resp.headers.get("Content-Type", "").lower()
            logger.info(f"URL: {url}")
            logger.info(f"Content-Type: {content_type}")

            if "application/vnd.apple.mpegurl" in content_type or "application/x-mpegurl" in content_type:
                return "HLS"
            if "application/dash+xml" in content_type:
                return "DASH"
            if "video/mp4" in content_type:
                return "MP4"
            if "video/webm" in content_type:
                return "WebM"
            if "video/mp2t" in content_type:
                return "TS"
        except requests.RequestException as e:
            print(f"Content-Type check failed: {e}")
            # Continue to byte inspection

        # --- 3. As fallback, peek at first 1024 bytes ---
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Range': 'bytes=0-1023'  # Only get first 1024 bytes
            }
            sample = requests.get(url, stream=True, timeout=10, headers=headers)
            data = sample.raw.read(1024)
            logger.info(f"First 1024 bytes: {data[:64]}...")

            # Check if data is gzipped and decompress if needed
            if data.startswith(b'\x1f\x8b\x08'):  # gzip magic number
                try:
                    # Try to get more data for proper decompression
                    headers_full = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept-Encoding': 'gzip, deflate'
                    }
                    full_response = requests.get(url, headers=headers_full, timeout=15)

                    # Check if response was gzipped
                    if full_response.headers.get('content-encoding') == 'gzip':
                        # requests automatically decompresses gzipped responses
                        data = full_response.content[:1024]
                        logger.info(f"Successfully decompressed gzipped content: {data[:64]}...")
                    else:
                        # Manual decompression for partial data
                        decompressed = gzip.decompress(data)
                        data = decompressed
                        logger.info(f"Manually decompressed gzipped data: {data[:64]}...")

                except Exception as e:
                    logger.warning(f"Failed to decompress gzipped data: {e}")
                    # If it's HTML content that's gzipped, try to detect it anyway
                    if b'<html' in data.lower() or b'<body' in data.lower() or b'<!doctype' in data.lower():
                        logger.info("Detected HTML content in compressed data - likely a webpage, not a video stream")
                        return "HTML webpage"
                    # Continue with original data

            # Check if this is HTML content (webpage instead of video stream)
            data_lower = data.lower()
            if (b'<html' in data_lower or b'<body' in data_lower
                    or b'<!doctype' in data_lower or b'<head>' in data_lower):
                logger.warning("Detected HTML content - URL points to webpage, not video stream")
                return "HTML webpage"

            if data.startswith(b"#EXTM3U"):
                # Check if it's M4S-based HLS (fragmented MP4)
                logger.info("DEBUG: HLS playlist detected, checking for M4S content...")
                logger.info("DEBUG: Playlist sample (first 500 bytes): %s", data[:500])
                if b".m4s" in data.lower() or b"/m4s" in data.lower():
                    logger.info("DEBUG: M4S content found - returning HLS_M4S")
                    return "HLS_M4S"
                logger.info("DEBUG: No M4S content found - returning HLS")
                return "HLS"
            if data.startswith(b"\x00\x00\x00") and b"ftyp" in data[:32]:
                return "MP4"
            if b"ftypmp4" in data[:32] or b"ftypisom" in data[:32]:
                return "MP4"
            if data.startswith(b"ID3") or data[0:1] == b"G":
                return "TS"
            if data.lstrip().startswith(b"<MPD"):
                return "DASH"
            if b"webm" in data[:100].lower():
                return "WebM"
        except requests.RequestException as e:
            print(f"Byte inspection failed: {e}")

        return "Unknown stream type"

    except Exception as e:
        return f"Error: {e}"


def resolve_url_with_quality_selection(sources, preferred_quality="best"):
    """
    Central quality selection function for providers.

    Args:
        sources (list): List of source dictionaries with 'quality', 'url', etc. keys
        preferred_quality (str): Preferred quality ("best" for highest, or specific like "720p")

    Returns:
        str or None: Selected URL string or None if no sources available
    """
    if not sources:
        logger.warning("No sources provided for quality selection")
        return None

    # If only one source, return its URL
    if len(sources) == 1:
        url = sources[0].get("url", "")
        if url:
            logger.info("Only one source available, selecting: %s", sources[0].get("quality", "unknown"))
            return url
        return None

    # If requesting best quality (default), sources are already sorted by resolvers
    # so just take the first one
    if preferred_quality == "best":
        best_source = sources[0]
        url = best_source.get("url", "")
        if url:
            logger.info("Selected best quality source: %s", best_source.get("quality", "unknown"))
            return url
        return None

    # Look for specific quality match
    for source in sources:
        source_quality = source.get("quality", "").lower()
        if source_quality == preferred_quality.lower():
            url = source.get("url", "")
            if url:
                logger.info("Found exact quality match: %s", source_quality)
                return url

    # If no exact match, find closest quality using scoring
    target_score = get_quality_score(preferred_quality)
    best_source = None
    best_diff = float('inf')

    for source in sources:
        source_quality = source.get("quality", "")
        source_score = get_quality_score(source_quality)
        diff = abs(source_score - target_score)

        if diff < best_diff:
            best_diff = diff
            best_source = source

    if best_source:
        url = best_source.get("url", "")
        if url:
            logger.info("Selected closest quality match: %s (target: %s)",
                        best_source.get("quality", "unknown"), preferred_quality)
            return url

    # Final fallback - return first source URL
    fallback_source = sources[0]
    url = fallback_source.get("url", "")
    if url:
        logger.info("Using fallback source: %s", fallback_source.get("quality", "unknown"))
        return url

    return None


def get_quality_score(quality: str) -> int:
    """Convert quality string to numeric score for comparison"""
    quality_lower = quality.lower()
    if "4k" in quality_lower or "2160" in quality_lower:
        return 4000
    if "1440" in quality_lower:
        return 1440
    if "1080" in quality_lower:
        return 1080
    if "720" in quality_lower:
        return 720
    if "480" in quality_lower:
        return 480
    if "360" in quality_lower:
        return 360
    if "240" in quality_lower:
        return 240
    if "144" in quality_lower:
        return 144
    return 500  # Default score for unknown quality


def select_best_hls_template_quality(available_qualities, preferred_quality="720p"):
    """
    Select the best available quality for HLS template streams.

    Args:
        available_qualities (list): List of available quality strings (e.g., ["1080p", "720p", "480p"])
        preferred_quality (str): Preferred quality to select if available

    Returns:
        str or None: Selected quality string or None if no qualities available
    """
    if not available_qualities:
        logger.warning("No available qualities provided, using fallback: %s", preferred_quality)
        return preferred_quality

    # Priority order: 1080p, 720p, 480p, 360p, 240p, 144p
    quality_preference = ["1080p", "720p", "480p", "360p", "240p", "144p"]

    # First try to find preferred quality
    if preferred_quality in available_qualities:
        logger.info("Selected preferred quality: %s", preferred_quality)
        return preferred_quality

    # Then try quality preference order
    for pref_qual in quality_preference:
        if pref_qual in available_qualities:
            logger.info("Selected quality from preference order: %s", pref_qual)
            return pref_qual

    # If no preferred quality found, use the highest available by numeric value
    numeric_qualities = []
    for q in available_qualities:
        try:
            # Extract numeric value (e.g., "720p" -> 720)
            numeric_value = int(q.replace('p', ''))
            numeric_qualities.append((numeric_value, q))
        except ValueError:
            continue

    if numeric_qualities:
        numeric_qualities.sort(reverse=True)  # Highest first
        selected_quality = numeric_qualities[0][1]
        logger.info("Selected highest numeric quality: %s", selected_quality)
        return selected_quality

    # Fallback to first available
    selected_quality = available_qualities[0]
    logger.info("Selected fallback quality (first available): %s", selected_quality)
    return selected_quality


def select_best_source(sources, preferred_quality="best"):
    """
    Select the best source from a list of sources with quality information.

    Args:
        sources (list): List of source dictionaries with 'quality', 'url', etc. keys
        preferred_quality (str): Preferred quality ("best" for highest, or specific like "720p")

    Returns:
        dict or None: Selected source dictionary or None if no sources available
    """
    if not sources:
        logger.warning("No sources provided for selection")
        return None

    # If only one source, return it
    if len(sources) == 1:
        logger.info("Only one source available, selecting: %s", sources[0].get("quality", "unknown"))
        return sources[0]

    # If requesting best quality (default), sources are already sorted by resolvers
    # so just take the first one
    if preferred_quality == "best":
        best_source = sources[0]
        logger.info("Selected best quality source: %s", best_source.get("quality", "unknown"))
        return best_source

    # Look for specific quality match
    for source in sources:
        source_quality = source.get("quality", "").lower()
        if source_quality == preferred_quality.lower():
            logger.info("Found exact quality match: %s", source_quality)
            return source

    # If no exact match, find closest quality using scoring
    target_score = get_quality_score(preferred_quality)
    best_source = None
    best_diff = float('inf')

    for source in sources:
        source_quality = source.get("quality", "")
        source_score = get_quality_score(source_quality)
        diff = abs(source_score - target_score)

        if diff < best_diff:
            best_diff = diff
            best_source = source

    if best_source:
        logger.info("Selected closest quality match: %s (target: %s)",
                    best_source.get("quality", "unknown"), preferred_quality)
        return best_source

    # Final fallback - return first source
    fallback_source = sources[0]
    logger.info("Using fallback source: %s", fallback_source.get("quality", "unknown"))
    return fallback_source

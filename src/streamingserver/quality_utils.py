#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Simplified Quality Selection Utilities

This module provides a clean, simple approach to quality selection using
closest-match logic rather than complex scoring algorithms.
"""

import re
from debug import get_logger
from hls_quality_analyzer import enhance_sources_with_hls_quality

logger = get_logger(__file__)

# Quality constants - ordered from highest to lowest quality
# "adaptive" represents HLS adaptive streaming and should be treated as highest quality
QUALITY_PRIORITY_ORDER = ["adaptive", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]


def extract_metadata_from_url(url):
    """
    Extract quality, format, and codec information from URL patterns.

    This provides a centralized way for resolvers to extract metadata from URLs
    without duplicating pattern matching logic.

    Args:
        url (str): Video URL to analyze

    Returns:
        dict: Dictionary with extracted metadata:
            - quality (str): Quality level (e.g., "720p", "1080p") or None if not found
            - format (str): Format type ("mp4", "m3u8") or None if not determined
            - codec (str): Codec (e.g., "h264", "h265", "av1") or None if not found

    Example:
        >>> extract_metadata_from_url("https://cdn.example.com/video_1080p_h264.mp4")
        {"quality": "1080p", "format": "mp4", "codec": "h264"}

        >>> extract_metadata_from_url("https://stream.example.com/master.m3u8")
        {"quality": None, "format": "m3u8", "codec": None}
    """
    if not url:
        return {"quality": None, "format": None, "codec": None}

    url_lower = url.lower()

    # Extract format from file extension
    format_type = None
    if '.m3u8' in url_lower:
        format_type = "m3u8"
    elif '.mp4' in url_lower:
        format_type = "mp4"

    # Extract quality from URL path (ignore query parameters)
    quality = None
    path_part = url.split('?')[0]  # Remove query parameters to avoid false matches

    # Try to extract explicit quality label first (e.g., "720p", "1080p")
    # Only match valid quality values to avoid bogus numbers like 34p, 3p, 9p, etc.
    quality_match = re.search(r'\b(2160|1440|1080|720|480|360|240|144)p\b', path_part, re.IGNORECASE)
    if quality_match:
        quality = quality_match.group(0).lower()  # Use group(0) to include 'p' suffix
    # Fallback: Try to infer quality from numeric values or keywords
    elif '2160' in url_lower or '4k' in url_lower:
        quality = "2160p"
    elif '1440' in url_lower:
        quality = "1440p"
    elif '1080' in url_lower:
        quality = "1080p"
    elif '720' in url_lower or 'hd' in url_lower or 'hq' in url_lower:
        quality = "720p"
    elif '480' in url_lower:
        quality = "480p"
    elif '360' in url_lower:
        quality = "360p"
    elif '240' in url_lower:
        quality = "240p"
    elif '144' in url_lower:
        quality = "144p"

    # Extract codec from URL patterns
    codec = None
    if any(pattern in url_lower for pattern in ('.av1.', '_av1_', 'av1-', '/av1/')):
        codec = "av1"
    elif any(pattern in url_lower for pattern in ('.h265.', '_h265_', '.hevc.', '_hevc_', 'h265-', 'hevc-')):
        codec = "h265"
    elif any(pattern in url_lower for pattern in ('.h264.', '_h264_', '.avc.', '_avc_', 'h264-', 'avc-')):
        codec = "h264"

    return {
        "quality": quality,
        "format": format_type,
        "codec": codec
    }


def select_best_source(sources, preferred_quality="best", codec_aware=True, av1=None, debug_output=True, analyze_hls=True):
    """
    Select the best source using simple closest quality matching.

    Args:
        sources (list): List of source dictionaries
        preferred_quality (str): Target quality ("best", "1080p", "720p", "adaptive", etc.)
        codec_aware (bool): Whether to prefer better codecs when quality matches
        av1 (bool): Whether to include AV1 codecs
        debug_output (bool): Whether to show detailed source selection info
        analyze_hls (bool): Whether to analyze HLS streams for actual quality levels

    Returns:
        dict: Selected source
    """
    if not sources:
        logger.warning("No sources provided for selection")
        return None

    # All sources must be dictionaries
    if not all(isinstance(source, dict) for source in sources):
        raise ValueError("All sources must be dictionaries with 'quality', 'format', and 'url' keys")

    # Enhance sources with HLS quality analysis if requested
    if analyze_hls:
        logger.info("HLS analysis enabled: analyze_hls=%s", analyze_hls)
        try:
            # Analyze any m3u8 sources (HLS streams), regardless of current quality label
            # This handles cases where extract_metadata_from_url() may have extracted
            # an incorrect quality from the URL path
            hls_sources = [s for s in sources if s.get('format') == 'm3u8']
            logger.info("Found %d m3u8 sources for potential HLS analysis", len(hls_sources))
            if hls_sources:
                logger.info("Analyzing %d HLS sources for quality information...", len(hls_sources))
                enhanced_sources = enhance_sources_with_hls_quality(sources)
                if enhanced_sources:
                    sources = enhanced_sources
                    logger.info("HLS quality enhancement completed")
                else:
                    logger.warning("HLS enhancement returned None or empty list")
        except Exception as e:
            logger.error("HLS quality analysis failed with exception: %s", e, exc_info=True)
    else:
        logger.info("HLS analysis disabled: analyze_hls=%s", analyze_hls)

    # Debug output: Show all available sources
    if debug_output:
        logger.info("=== QUALITY SELECTION DEBUG ===")
        logger.info("Available sources (%d total):", len(sources))
        for i, source in enumerate(sources, 1):
            quality = source.get("quality", "unknown")
            format_type = source.get("format", "unknown")
            codec = source.get("codec", "")
            url = source.get("url", "")
            codec_info = f" [{codec}]" if codec else ""

            # Show HLS enhancement info if available
            hls_info = ""
            if source.get('hls_analysis'):
                hls_data = source['hls_analysis']
                if hls_data.get('qualities'):
                    available_qualities = ', '.join(hls_data['qualities'])
                    hls_info = f" (HLS: {available_qualities})"
                elif source.get('original_quality') == 'adaptive':
                    hls_info = " (Enhanced from adaptive)"

            logger.info("  %d. %s (%s)%s%s - %s", i, quality, format_type, codec_info, hls_info, url)

        logger.info("Selection criteria: quality='%s', codec_aware=%s, av1=%s",
                    preferred_quality, codec_aware, av1)

    # If only one source, return it
    if len(sources) == 1:
        selected = sources[0]
        logger.info("Only one source available, selecting: %s (%s)",
                    selected.get("quality", "unknown"), selected.get("format", "unknown"))
        if debug_output:
            logger.info("=== SELECTION RESULT: %s ===", selected.get("quality", "unknown"))
        return selected

    # If "best", select highest quality available
    if preferred_quality == "best":
        best_source = _select_highest_quality_source(sources, codec_aware, av1)
        logger.info("Selected best quality source: %s (%s)",
                    best_source.get("quality", "unknown"), best_source.get("format", "unknown"))
        if debug_output:
            logger.info("=== SELECTION RESULT: %s ===", best_source.get("quality", "unknown"))
        return best_source

    # Find exact match first
    exact_match = _find_exact_quality_match(sources, preferred_quality)
    if exact_match:
        # If multiple exact matches, prefer better codec if codec_aware
        if len(exact_match) == 1:
            selected = exact_match[0]
            # Check if this was matched because HLS contains the quality
            if (
                    selected.get('hls_analysis')
                    and preferred_quality.lower() in [q.lower() for q in selected['hls_analysis']['qualities']]
                    and selected.get('quality', '').lower() != preferred_quality.lower()
            ):
                logger.info("Found exact quality match in HLS stream: %s (contains %s)",
                            selected.get("quality", "unknown"), preferred_quality)
            else:
                logger.info("Found exact quality match: %s", selected.get("quality", "unknown"))
            return selected
        if codec_aware:
            selected = _select_best_codec(exact_match, av1)
            logger.info("Found exact quality match with preferred codec: %s (%s)",
                        selected.get("quality", "unknown"),
                        _get_codec(selected))
            return selected
        selected = exact_match[0]
        logger.info("Found exact quality match: %s (%s)",
                    selected.get("quality", "unknown"), selected.get("format", "unknown"))
        if debug_output:
            logger.info("=== SELECTION RESULT: %s ===", selected.get("quality", "unknown"))
        return selected

    # Find closest quality match
    closest_source = _find_closest_quality_match(sources, preferred_quality)
    logger.info("Selected closest quality match: %s (%s) - target: %s",
                closest_source.get("quality", "unknown"), closest_source.get("format", "unknown"), preferred_quality)
    if debug_output:
        logger.info("=== SELECTION RESULT: %s ===", closest_source.get("quality", "unknown"))
    return closest_source


def _select_highest_quality_source(sources, codec_aware, av1):
    """Select the highest quality source from available sources."""
    # Filter out 2160p (4K) content if AV1 is disabled, as 4K typically requires AV1
    filtered_sources = sources
    if av1 is False and codec_aware:
        filtered_sources = []
        for source in sources:
            quality = source.get("quality", "unknown")
            if quality == "2160p":
                logger.debug("Filtering out 2160p source due to av1=False: %s", source.get("url", ""))
                continue
            filtered_sources.append(source)

        # If all sources were filtered out, keep the original sources
        if not filtered_sources:
            logger.warning("All sources filtered out due to av1=False, keeping original sources")
            filtered_sources = sources

    # Sort sources by quality priority (highest first)
    def quality_priority(source):
        quality = source.get("quality", "unknown")
        try:
            return QUALITY_PRIORITY_ORDER.index(quality)
        except ValueError:
            return len(QUALITY_PRIORITY_ORDER)  # Unknown qualities go to end

    sorted_sources = sorted(filtered_sources, key=quality_priority)

    if not codec_aware:
        return sorted_sources[0]

    # Get all sources with the highest quality
    highest_quality = sorted_sources[0].get("quality")
    highest_quality_sources = [s for s in sorted_sources if s.get("quality") == highest_quality]

    if len(highest_quality_sources) == 1:
        return highest_quality_sources[0]

    # Multiple sources with same highest quality - prefer better codec
    return _select_best_codec(highest_quality_sources, av1)


def _find_exact_quality_match(sources, target_quality):
    """Find sources with exact quality match, including HLS streams with that quality."""
    direct_matches = []  # Sources where quality directly matches
    hls_matches = []     # HLS sources that contain the quality

    for source in sources:
        source_quality = source.get("quality", "").lower()

        # Direct quality match - highest priority
        if source_quality == target_quality.lower():
            direct_matches.append(source)
            continue

        # Check if HLS stream contains the target quality - lower priority
        hls_analysis = source.get('hls_analysis')
        if hls_analysis and hls_analysis.get('qualities'):
            available_qualities = [q.lower() for q in hls_analysis['qualities']]
            if target_quality.lower() in available_qualities:
                hls_matches.append(source)
                continue

    # Prefer direct matches over HLS matches
    # Direct matches are better because they're typically direct MP4 files
    # HLS matches require playlist parsing and may have overhead
    if direct_matches:
        return direct_matches
    return hls_matches


def _find_closest_quality_match(sources, target_quality):
    """Find the source with quality closest to target, considering HLS stream contents."""
    # Try to parse numeric value from target quality
    target_numeric = _extract_numeric_quality(target_quality)

    # If target is numeric, only consider numeric sources for closest match
    if target_numeric is not None:
        numeric_sources = []
        for source in sources:
            quality = source.get("quality", "unknown")
            source_numeric = _extract_numeric_quality(quality)

            # Check if this source has the exact target quality (especially in HLS streams)
            hls_analysis = source.get('hls_analysis')
            if hls_analysis and hls_analysis.get('qualities'):
                # Check if any HLS quality exactly matches target
                for hls_quality in hls_analysis['qualities']:
                    hls_numeric = _extract_numeric_quality(hls_quality)
                    if hls_numeric == target_numeric:
                        # Exact match in HLS stream - give it priority (distance = 0)
                        numeric_sources.append((source, 0))
                        break
                else:
                    # No exact match, use the stream's maximum quality for distance calculation
                    if source_numeric is not None:
                        distance = abs(source_numeric - target_numeric)
                        # Prefer lower quality over much higher quality (bandwidth consideration)
                        # If source is significantly higher than target, penalize it more
                        if source_numeric > target_numeric:
                            distance *= 1.5  # 50% penalty for exceeding target
                        numeric_sources.append((source, distance))
            elif source_numeric is not None:
                distance = abs(source_numeric - target_numeric)
                # Prefer lower quality over much higher quality (bandwidth consideration)
                # If source is significantly higher than target, penalize it more
                if source_numeric > target_numeric:
                    distance *= 1.5  # 50% penalty for exceeding target
                numeric_sources.append((source, distance))

        # If we found numeric sources, return the closest one
        if numeric_sources:
            numeric_sources.sort(key=lambda x: x[1])  # Sort by distance
            return numeric_sources[0][0]  # Return source with smallest distance

    # For non-numeric targets, use priority order comparison
    best_source = None
    best_distance = float('inf')

    for source in sources:
        quality = source.get("quality", "unknown")

        # Check HLS streams for exact match first
        hls_analysis = source.get('hls_analysis')
        if hls_analysis and hls_analysis.get('qualities'):
            if target_quality in hls_analysis['qualities']:
                return source  # Exact match in HLS stream

        try:
            target_index = QUALITY_PRIORITY_ORDER.index(target_quality)
            source_index = QUALITY_PRIORITY_ORDER.index(quality)
            distance = abs(source_index - target_index)
            if distance < best_distance:
                best_distance = distance
                best_source = source
        except ValueError:
            # Unknown quality - skip it (no fallback behavior)
            continue

    # If no match found in priority order, return first source
    return best_source or sources[0]


def _extract_numeric_quality(quality_str):
    """Extract numeric value from quality string (e.g., '1080p' -> 1080)."""
    if not quality_str:
        return None

    # Extract numeric part only - no legacy alias support
    match = re.search(r'(\d+)', quality_str)
    if match:
        return int(match.group(1))

    return None


def _select_best_codec(sources_with_same_quality, av1):
    """Select source with best codec from sources with same quality."""
    # Filter AV1 if disabled
    filtered_sources = sources_with_same_quality
    if av1 is False:
        non_av1_sources = []
        for source in sources_with_same_quality:
            codec = _get_codec(source)
            if codec.lower() != "av1":
                non_av1_sources.append(source)
        if non_av1_sources:
            filtered_sources = non_av1_sources

    # Simple codec preference: av1 > h265 > h264 > others
    # Also prefer direct formats (MP4) over HLS when codec is unknown or equal
    codec_priority = {"av1": 0, "h265": 1, "hevc": 1, "h264": 2, "avc": 2}

    def codec_score(source):
        codec = _get_codec(source).lower()
        codec_value = codec_priority.get(codec, 99)  # Unknown codecs get low priority

        # Add format preference as tiebreaker: prefer mp4 over m3u8
        # This ensures that when codecs are equal or unknown, we prefer direct MP4 files
        format_penalty = 0.0
        if source.get('format') == 'm3u8':
            format_penalty = 0.1  # Small penalty for HLS

        return codec_value + format_penalty

    return min(filtered_sources, key=codec_score)


def _get_codec(source):
    """Extract codec from source object."""
    codec = source.get("codec", "")
    if codec:
        return codec

    # Try to extract from URL
    url = source.get("url", "")
    if not url:
        return "unknown"

    url_lower = url.lower()
    if any(pattern in url_lower for pattern in ('.av1.', '_av1_', 'av1-')):
        return "av1"
    if any(pattern in url_lower for pattern in ('.h265.', '_h265_', '.hevc.', '_hevc_')):
        return "h265"
    if any(pattern in url_lower for pattern in ('.h264.', '_h264_', '.avc.', '_avc_')):
        return "h264"

    return "unknown"

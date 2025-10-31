#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Simplified Quality Selection Utilities

This module provides quality selection using a priority-based scoring system:
- Quality match (exact > distance-based with penalty for exceeding)
- Codec preference (AV1 > H.265 > H.264)
- Format preference (MP4 > M3U8)

Quality levels supported: 2160p, 1440p, 1080p, 720p, 480p, 360p, 240p, 144p
Special handling:
- "best" → mapped to "2160p" (selects highest available quality)
- "adaptive" → expanded into multiple quality variants during HLS analysis
"""

import re
from debug import get_logger
from hls_quality_analyzer import enhance_sources_with_hls_quality

logger = get_logger(__file__)

# Quality constants - ordered from highest to lowest quality
QUALITY_PRIORITY_ORDER = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]


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


def select_best_source(sources, preferred_quality="best", codec_aware=True, av1=None, analyze_hls=True):
    """
    Select the best source using priority-based scoring.

    Scoring priorities (in order):
    1. Quality match (exact > closest)
    2. Codec preference (av1 > h265 > h264, filtered by av1 param)
    3. Format preference (mp4 > m3u8)

    Args:
        sources (list): List of source dictionaries
        preferred_quality (str): Target quality ("best", "1080p", "720p", etc.)
                                "best" is treated as "2160p" (selects highest available)
        codec_aware (bool): Whether to prefer better codecs when quality matches
        av1 (bool): Whether to include AV1 codecs
        analyze_hls (bool): Whether to analyze HLS streams for actual quality levels

    Returns:
        dict: Selected source or None if all filtered out
    """
    if not sources:
        logger.warning("No sources provided for selection")
        return None

    # All sources must be dictionaries
    if not all(isinstance(source, dict) for source in sources):
        raise ValueError("All sources must be dictionaries with 'quality', 'format', and 'url' keys")

    # Map "best" to highest quality for simpler logic
    if preferred_quality.lower() == "best":
        preferred_quality = "2160p"  # Highest quality - algorithm will find closest available

    # Enhance sources with HLS quality analysis if requested
    if analyze_hls:
        logger.info("HLS analysis enabled: analyze_hls=%s", analyze_hls)
        try:
            hls_sources = [s for s in sources if s.get('format') == 'm3u8']
            logger.info("Found %d m3u8 sources for potential HLS analysis", len(hls_sources))
            if hls_sources:
                logger.info("Analyzing %d HLS sources for quality information...", len(hls_sources))
                enhanced_sources = enhance_sources_with_hls_quality(sources)
                if enhanced_sources:
                    logger.info("HLS enhancement: %d sources -> %d sources", len(sources), len(enhanced_sources))
                    sources = enhanced_sources
                else:
                    logger.warning("HLS enhancement returned None or empty list")
        except Exception as e:
            logger.error("HLS quality analysis failed with exception: %s", e, exc_info=True)
    else:
        logger.info("HLS analysis disabled: analyze_hls=%s", analyze_hls)

    # Always log input sources summary
    logger.info("=== QUALITY SELECTION START ===")
    logger.info("Input: %d sources, target quality: %s, codec_aware: %s, av1: %s",
                len(sources), preferred_quality, codec_aware, av1)

    # Log each input source
    for i, source in enumerate(sources, 1):
        quality = source.get("quality", "unknown")
        format_type = source.get("format", "unknown")
        codec = source.get("codec", "unknown")
        url = source.get("url", "")

        # Show HLS enhancement info if available
        hls_info = ""
        if source.get('hls_analysis'):
            hls_data = source['hls_analysis']
            if hls_data.get('from_adaptive'):
                hls_info = " [from adaptive]"
            elif hls_data.get('qualities'):
                available_qualities = ', '.join(hls_data['qualities'])
                hls_info = f" [HLS: {available_qualities}]"

        logger.info("  Source %d: %s (%s) codec:%s%s - %s",
                    i, quality, format_type, codec, hls_info, url)

    # Filter out AV1 sources if av1=False and codec_aware=True
    filtered_sources = sources
    if av1 is False and codec_aware:
        non_av1_sources = [s for s in sources if _get_codec(s).lower() != "av1"]
        if non_av1_sources:
            filtered_out = len(sources) - len(non_av1_sources)
            filtered_sources = non_av1_sources
            logger.info("Filtered out %d AV1 sources (av1=False)", filtered_out)
        else:
            logger.warning("All sources are AV1 but av1=False, returning None")
            logger.info("=== SELECTION RESULT: None (all AV1, disabled) ===")
            return None

    # Filter out 2160p when av1=False (4K typically requires AV1)
    if av1 is False and codec_aware:
        non_4k_sources = [s for s in filtered_sources if s.get("quality") != "2160p"]
        if non_4k_sources and len(non_4k_sources) < len(filtered_sources):
            logger.info("Filtered out %d 2160p sources (av1=False)", len(filtered_sources) - len(non_4k_sources))
            filtered_sources = non_4k_sources

    # Score each source and select the best
    scored_sources = []
    for source in filtered_sources:
        score = _score_source(source, preferred_quality, codec_aware)
        scored_sources.append((score, source))

    # Sort by score (higher is better)
    scored_sources.sort(key=lambda x: x[0], reverse=True)

    best_score, best_source = scored_sources[0]

    # Always log scoring results (top 5)
    logger.info("=== SCORING RESULTS (Top 5) ===")
    for i, (score, source) in enumerate(scored_sources[:5], 1):
        logger.info("  %d. Score: %.2f - %s (%s) codec:%s - %s",
                    i, score,
                    source.get("quality", "unknown"),
                    source.get("format", "unknown"),
                    _get_codec(source),
                    source.get("url", ""))

    # Always log final selection
    logger.info("=== SELECTED SOURCE ===")
    logger.info("Quality: %s, Format: %s, Codec: %s, Score: %.2f",
                best_source.get("quality", "unknown"),
                best_source.get("format", "unknown"),
                _get_codec(best_source),
                best_score)
    logger.info("URL: %s", best_source.get("url", ""))

    return best_source


def _score_source(source, target_quality, codec_aware):
    """
    Score a source based on quality match, codec, and format.
    Higher score = better match.

    Scoring breakdown:
    - Quality match: 0-1000 points (exact match, distance-based)
    - Codec: 0-100 points (av1=100, h265=75, h264=50, unknown=25)
    - Format: 0-10 points (mp4=10, m3u8=5)
    """
    score = 0.0

    # 1. Quality match score (0-1000 points)
    quality_score = _score_quality_match(source, target_quality)
    score += quality_score

    # 2. Codec score (0-100 points, only if codec_aware)
    if codec_aware:
        codec_score = _score_codec(source)
        score += codec_score

    # 3. Format score (0-10 points)
    format_score = _score_format(source)
    score += format_score

    return score


def _score_quality_match(source, target_quality):
    """
    Score quality match. Higher is better.

    Scoring:
    - Exact match: 1000 points
    - Numeric match: inverse distance score with penalty for exceeding
    - HLS containing target: 900 points
    - Non-numeric priority order match: distance-based
    - Unknown: 0 points
    """
    source_quality = source.get("quality", "unknown").lower()
    target_quality_lower = target_quality.lower()

    # Exact match - highest priority
    if source_quality == target_quality_lower:
        return 1000.0

    # Check if HLS stream contains target quality
    hls_analysis = source.get('hls_analysis')
    if hls_analysis and hls_analysis.get('qualities'):
        available_qualities = [q.lower() for q in hls_analysis['qualities']]
        if target_quality_lower in available_qualities:
            return 900.0  # Slightly less than exact match

    # Numeric quality matching - distance-based
    target_numeric = _extract_numeric_quality(target_quality)
    source_numeric = _extract_numeric_quality(source_quality)

    if target_numeric is not None and source_numeric is not None:
        distance = abs(source_numeric - target_numeric)

        # Apply 2x penalty for exceeding target (bandwidth consideration)
        if source_numeric > target_numeric:
            distance *= 2.0

        # Convert distance to score (max 800 points, decreases with distance)
        # Using exponential decay: score = 800 * e^(-distance/200)
        quality_score = 800.0 * (2.71828 ** (-distance / 200.0))
        return quality_score

    # Non-numeric quality - try priority order
    try:
        target_index = QUALITY_PRIORITY_ORDER.index(target_quality_lower)
        source_index = QUALITY_PRIORITY_ORDER.index(source_quality)
        distance = abs(source_index - target_index)
        # Closer in priority list = better score
        return max(0, 700.0 - (distance * 100))
    except ValueError:
        return 0.0  # Unknown quality


def _score_codec(source):
    """
    Score codec preference. Higher is better.

    Scoring:
    - av1: 100 points
    - h265/hevc: 75 points
    - h264/avc: 50 points
    - unknown: 25 points
    """
    codec = _get_codec(source).lower()

    codec_scores = {
        "av1": 100.0,
        "h265": 75.0,
        "hevc": 75.0,
        "h264": 50.0,
        "avc": 50.0,
        "unknown": 25.0,
    }

    return codec_scores.get(codec, 25.0)


def _score_format(source):
    """
    Score format preference. Higher is better.

    Scoring:
    - mp4: 10 points (direct file)
    - m3u8: 5 points (streaming)
    - unknown: 0 points
    """
    format_type = source.get("format", "unknown").lower()

    format_scores = {
        "mp4": 10.0,
        "m3u8": 5.0,
    }

    return format_scores.get(format_type, 0.0)


def _extract_numeric_quality(quality_str):
    """Extract numeric value from quality string (e.g., '1080p' -> 1080)."""
    if not quality_str:
        return None

    # Extract numeric part only - no legacy alias support
    match = re.search(r'(\d+)', quality_str)
    if match:
        return int(match.group(1))

    return None


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

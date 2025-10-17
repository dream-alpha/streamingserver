#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Video Quality Processing Utilities

This module provides centralized constants and utility functions for video quality
processing, standardizing quality handling across recorders, resolvers, and providers.
"""

from __future__ import annotations

import re
from typing import Any
from debug import get_logger

logger = get_logger(__file__)

# Quality constants - ordered from highest to lowest quality
QUALITY_PRIORITY_ORDER = ["4K", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]

# Default quality lists for different contexts
DEFAULT_AVAILABLE_QUALITIES = ["1080p", "720p", "480p", "360p", "240p"]

# Quality mapping for different naming conventions
QUALITY_ALIASES = {
    "UHD": "2160p",
    "4K": "2160p",
    "QHD": "1440p",
    "2K": "1440p",
    "FHD": "1080p",
    "Full HD": "1080p",
    "HD": "720p",
    "high": "720p",
    "medium": "480p",
    "low": "360p",
    "SD": "480p",
    "HLS": "720p",   # HLS typically contains multiple qualities, assume good quality
    "auto": "best",  # Adaptive streaming
}

# Codec preferences (higher score = better codec for modern streaming)
CODEC_SCORES = {
    "av1": 100,      # Best compression, modern codec
    "h265": 80,      # Good compression, widely supported
    "hevc": 80,      # Same as h265
    "h264": 60,      # Standard codec, universal support
    "avc": 60,       # Same as h264
    "vp9": 40,       # Good for web, less hardware support
    "vp8": 20,       # Older codec
    "unknown": 0,    # Unknown codec
}

# AV1 codec configuration
AV1_ENABLED = True  # Set to False to disable AV1 preference

# Quality scoring for sorting (higher score = better quality)
QUALITY_SCORES = {
    "4K": 15,
    "2160p": 15,
    "1440p": 12,
    "1080p": 10,
    "HD": 9,
    "720p": 8,
    "HLS": 7,     # HLS streams often contain multiple qualities
    "600p": 6,
    "540p": 5,
    "SD": 4,
    "480p": 4,
    "360p": 3,
    "240p": 2,
    "144p": 1,
    "Unknown": 0,
}

# Quality selectors for different use cases
QUALITY_SELECTORS = {
    "max": "2160p",           # Maximum quality available
    "uhd": "2160p",           # Ultra HD / 4K
    "fhd": "1080p",           # Full HD
    "hd": "720p",             # HD Ready
    "sd": "480p",             # Standard Definition
    "mobile": "360p",         # Mobile/low bandwidth
    "auto": "best",           # Let system choose best
    "adaptive": "best",       # Adaptive streaming
}


def normalize_quality(quality: str) -> str:
    """
    Normalize quality string to standard format.

    Args:
        quality (str): Raw quality string (e.g., "HD", "high", "720p")

    Returns:
        str: Normalized quality string (e.g., "720p")
    """
    if not quality:
        return "Unknown"

    # Convert to string and clean up
    quality = str(quality).strip()

    # Check for direct match first
    if quality in QUALITY_PRIORITY_ORDER:
        return quality

    # Check aliases
    if quality in QUALITY_ALIASES:
        return QUALITY_ALIASES[quality]

    # Try to extract numeric quality (e.g., "1280x720" -> "720p")
    numeric_match = re.search(r'(\d+)p', quality)
    if numeric_match:
        return numeric_match.group(0)

    # Try to extract from resolution (e.g., "1280x720" -> "720p")
    resolution_match = re.search(r'x(\d+)', quality)
    if resolution_match:
        height = int(resolution_match.group(1))
        return f"{height}p"

    logger.debug("Could not normalize quality: %s", quality)
    return quality


def get_quality_score(quality: str) -> int:
    """
    Get numeric score for a quality string for sorting purposes.

    Args:
        quality (str): Quality string

    Returns:
        int: Quality score (higher = better quality)
    """
    normalized = normalize_quality(quality)

    # Check direct score mapping
    if normalized in QUALITY_SCORES:
        return QUALITY_SCORES[normalized]

    # Try to extract numeric value for unlisted qualities
    numeric_match = re.search(r'(\d+)p', normalized)
    if numeric_match:
        numeric_value = int(numeric_match.group(1))
        # Scale numeric value to reasonable score range
        if numeric_value >= 1080:
            return 10
        if numeric_value >= 720:
            return 8
        if numeric_value >= 480:
            return 4
        if numeric_value >= 360:
            return 3
        return 1

    return 0


def select_best_quality(available_qualities: list[str], preferred_quality: str = "best") -> str:
    """
    Select the best quality from available options based on preference.

    Args:
        available_qualities (list): List of available quality strings
        preferred_quality (str): Preferred quality ("best" for highest, or specific like "720p")

    Returns:
        str: Selected quality string
    """
    if not available_qualities:
        logger.warning("No available qualities provided, using fallback: %s", preferred_quality)
        return preferred_quality if preferred_quality != "best" else "720p"

    # Normalize all available qualities
    normalized_qualities = [normalize_quality(q) for q in available_qualities]

    # If preferred quality is "best", select highest quality
    if preferred_quality == "best":
        return select_highest_quality(normalized_qualities)

    # Normalize preferred quality
    normalized_preferred = normalize_quality(preferred_quality)

    # First try to find exact match
    if normalized_preferred in normalized_qualities:
        logger.info("Selected preferred quality: %s", normalized_preferred)
        return normalized_preferred

    # Then try quality preference order
    for pref_qual in QUALITY_PRIORITY_ORDER:
        if pref_qual in normalized_qualities:
            logger.info("Selected quality from preference order: %s", pref_qual)
            return pref_qual

    # If no preferred quality found, use the highest available
    return select_highest_quality(normalized_qualities)


def select_highest_quality(available_qualities: list[str]) -> str:
    """
    Select the highest quality from available options.

    Args:
        available_qualities (list): List of available quality strings

    Returns:
        str: Highest quality string
    """
    if not available_qualities:
        return "720p"  # Fallback

    # Score and sort qualities
    scored_qualities = [(q, get_quality_score(q)) for q in available_qualities]
    scored_qualities.sort(key=lambda x: x[1], reverse=True)

    best_quality = scored_qualities[0][0]
    logger.info("Selected highest available quality: %s (score: %d)",
                best_quality, scored_qualities[0][1])
    return best_quality


def get_codec_score(codec: str) -> int:
    """
    Get numeric score for a codec for preference sorting.

    Args:
        codec (str): Codec identifier (e.g., 'av1', 'h265', 'h264')

    Returns:
        int: Codec score (higher = better/more preferred)
    """
    if not codec:
        return 0

    codec_lower = codec.lower().strip()
    return CODEC_SCORES.get(codec_lower, 0)


def extract_codec_from_url(url: str) -> str:
    """
    Extract codec information from URL if present.

    Args:
        url (str): Video URL that might contain codec indicators

    Returns:
        str: Detected codec or "unknown"
    """
    if not url:
        return "unknown"

    url_lower = url.lower()

    # Check for codec patterns in URL
    if any(pattern in url_lower for pattern in ('.av1.', '_av1_', 'av1-')):
        return "av1"
    if any(pattern in url_lower for pattern in ('.h265.', '_h265_', '.hevc.', '_hevc_')):
        return "h265"
    if any(pattern in url_lower for pattern in ('.h264.', '_h264_', '.avc.', '_avc_')):
        return "h264"
    if any(pattern in url_lower for pattern in ('.vp9.', '_vp9_')):
        return "vp9"
    if any(pattern in url_lower for pattern in ('.vp8.', '_vp8_')):
        return "vp8"

    return "unknown"


def sort_sources_by_quality_and_codec(sources: list[dict[str, Any]], quality_key: str = "quality", codec_key: str = "codec", av1: bool = None) -> list[dict[str, Any]]:
    """
    Sort video sources by quality and codec preference (best first).

    Args:
        sources (list): List of source dictionaries containing quality and codec information
        quality_key (str): Key name for quality field in source dictionaries
        codec_key (str): Key name for codec field in source dictionaries
        av1 (bool): Whether to include AV1 codecs (None=use global setting, True=force enable, False=force disable)

    Returns:
        list: Sorted sources with best quality/codec first
    """
    if not sources:
        return sources

    # Determine AV1 preference: parameter overrides global setting
    av1_enabled = av1 if av1 is not None else AV1_ENABLED

    def combined_sort_key(source):
        quality = source.get(quality_key, "Unknown")
        codec = source.get(codec_key, "unknown")

        # If no codec specified, try to extract from URL
        if codec == "unknown" and "url" in source:
            codec = extract_codec_from_url(source["url"])

        quality_score = get_quality_score(quality) * 100  # Weight quality higher
        codec_score = get_codec_score(codec) if av1_enabled or codec != "av1" else 0

        return quality_score + codec_score

    sorted_sources = sorted(sources, key=combined_sort_key, reverse=True)
    logger.debug("Sorted %d sources by quality and codec preference", len(sorted_sources))
    return sorted_sources


def sort_sources_by_quality(sources: list[dict[str, Any]], quality_key: str = "quality") -> list[dict[str, Any]]:
    """
    Sort video sources by quality (best first).

    Args:
        sources (list): List of source dictionaries containing quality information
        quality_key (str): Key name for quality field in source dictionaries

    Returns:
        list: Sorted sources with best quality first
    """
    if not sources:
        return sources

    def quality_sort_key(source):
        quality = source.get(quality_key, "Unknown")
        return get_quality_score(quality)

    sorted_sources = sorted(sources, key=quality_sort_key, reverse=True)
    logger.debug("Sorted %d sources by quality", len(sorted_sources))
    return sorted_sources


def select_quality_by_selector(available_qualities: list[str], selector: str) -> str:
    """
    Select quality using predefined quality selectors.

    Args:
        available_qualities (list): List of available quality strings
        selector (str): Quality selector ("max", "uhd", "fhd", "hd", "sd", "mobile", "auto")

    Returns:
        str: Selected quality string
    """
    if not available_qualities:
        return QUALITY_SELECTORS.get(selector, "720p")

    # Map selector to target quality
    target_quality = QUALITY_SELECTORS.get(selector.lower(), selector)

    # Use existing quality selection logic
    return select_best_quality(available_qualities, target_quality)


def filter_sources_by_codec(sources: list[dict[str, Any]], preferred_codecs: list[str] = None, codec_key: str = "codec") -> list[dict[str, Any]]:
    """
    Filter video sources to only include preferred codecs.

    Args:
        sources (list): List of source dictionaries
        preferred_codecs (list): List of preferred codec names (e.g., ['av1', 'h265', 'h264'])
        codec_key (str): Key name for codec field in source dictionaries

    Returns:
        list: Filtered sources containing only preferred codecs
    """
    if not sources or not preferred_codecs:
        return sources

    preferred_codecs_lower = [codec.lower() for codec in preferred_codecs]
    filtered_sources = []

    for source in sources:
        codec = source.get(codec_key, "unknown").lower()

        # If no codec specified, try to extract from URL
        if codec == "unknown" and "url" in source:
            codec = extract_codec_from_url(source["url"]).lower()

        if codec in preferred_codecs_lower:
            filtered_sources.append(source)

    logger.debug("Filtered %d sources to %d with preferred codecs: %s",
                 len(sources), len(filtered_sources), ", ".join(preferred_codecs))
    return filtered_sources


def configure_av1_support(enabled: bool) -> None:
    """
    Configure AV1 codec support globally.

    Args:
        enabled (bool): True to prefer AV1 codec, False to avoid it
    """
    global AV1_ENABLED  # pylint: disable=global-statement
    AV1_ENABLED = enabled
    logger.info("AV1 codec support %s", "enabled" if enabled else "disabled")


def get_codec_preferences() -> dict[str, int]:
    """
    Get current codec preferences with scores.

    Returns:
        dict: Dictionary mapping codec names to preference scores
    """
    return CODEC_SCORES.copy()


def get_quality_selectors() -> dict[str, str]:
    """
    Get available quality selectors and their target qualities.

    Returns:
        dict: Dictionary mapping selector names to target qualities
    """
    return QUALITY_SELECTORS.copy()

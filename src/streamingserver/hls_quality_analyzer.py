#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Quality Analyzer

This module provides utilities to analyze HLS master playlists and extract
available quality levels, resolutions, and bandwidths. This allows for better
quality selection when dealing with adaptive streaming sources.
"""

import m3u8
import requests
from debug import get_logger

logger = get_logger(__file__)


def analyze_hls_qualities(url, session=None):
    """
    Analyze an HLS master playlist URL to extract available quality levels.

    Args:
        url (str): URL to the HLS master playlist
        session (requests.Session, optional): Session to use for requests

    Returns:
        dict: Analysis results containing:
            - qualities: List of quality level strings (e.g., ['720p', '1080p'])
            - streams: List of stream info dicts with quality, bandwidth, resolution
            - max_quality: Highest quality level found
            - has_adaptive: Whether this is actually an adaptive stream
            - error: Error message if analysis failed
    """
    if not session:
        session = requests.Session()

    result = {
        'qualities': [],
        'streams': [],
        'max_quality': None,
        'has_adaptive': False,
        'error': None
    }

    try:
        logger.debug("Analyzing HLS playlist: %s", url)

        # Fetch the master playlist
        response = session.get(url, timeout=10)
        response.raise_for_status()

        # Parse with m3u8 library
        master_playlist = m3u8.loads(response.text)

        if not master_playlist.playlists:
            # This might be a direct media playlist, not a master playlist
            logger.debug("No sub-playlists found - this appears to be a direct media playlist")
            result['has_adaptive'] = False
            result['max_quality'] = 'adaptive'  # Fallback
            return result

        result['has_adaptive'] = True
        logger.debug("Found %d stream variants", len(master_playlist.playlists))

        # Extract quality information from each stream
        for playlist in master_playlist.playlists:
            stream_info = playlist.stream_info
            if not stream_info:
                continue

            stream_data = {
                'bandwidth': stream_info.bandwidth or 0,
                'resolution': None,
                'quality': None,
                'uri': playlist.uri
            }

            # Extract resolution if available
            if hasattr(stream_info, 'resolution') and stream_info.resolution:
                width, height = stream_info.resolution
                stream_data['resolution'] = f"{width}x{height}"
                # Convert resolution to quality label
                stream_data['quality'] = _resolution_to_quality(width, height)
            else:
                # Try to infer quality from bandwidth
                stream_data['quality'] = _bandwidth_to_quality(stream_info.bandwidth or 0)

            result['streams'].append(stream_data)

        # Sort streams by bandwidth (highest first)
        result['streams'].sort(key=lambda x: x['bandwidth'], reverse=True)

        # Extract unique quality levels
        qualities = []
        for stream in result['streams']:
            if stream['quality'] and stream['quality'] not in qualities:
                qualities.append(stream['quality'])

        result['qualities'] = qualities
        result['max_quality'] = qualities[0] if qualities else 'adaptive'

        logger.debug("HLS analysis complete: %d qualities found: %s",
                     len(qualities), ', '.join(qualities))

    except Exception as e:
        logger.error("Failed to analyze HLS playlist: %s", e)
        result['error'] = str(e)
        result['max_quality'] = 'adaptive'  # Fallback

    return result


def enhance_source_with_hls_quality(source, session=None):
    """
    Enhance a source dictionary by analyzing its HLS quality if it's an adaptive stream.

    Args:
        source (dict): Source dictionary with 'url', 'quality', 'format' keys
        session (requests.Session, optional): Session to use for requests

    Returns:
        dict: Enhanced source with additional HLS quality information
    """
    if not source:
        return source

    # Only analyze HLS streams (m3u8 format)
    if source.get('format') != 'm3u8' or not source.get('url'):
        return source

    logger.debug("Enhancing HLS source with quality analysis")

    # Analyze the HLS playlist
    analysis = analyze_hls_qualities(source['url'], session)

    if analysis['error']:
        logger.warning("HLS analysis failed for %s: %s", source['url'], analysis['error'])
        return source

    # Create enhanced source copy
    enhanced_source = source.copy()

    # Add HLS analysis data
    enhanced_source['hls_analysis'] = {
        'qualities': analysis['qualities'],
        'streams': analysis['streams'],
        'max_quality': analysis['max_quality'],
        'has_adaptive': analysis['has_adaptive']
    }

    # Update the quality to reflect the maximum available quality
    if analysis['max_quality'] and analysis['max_quality'] != 'adaptive':
        if source.get('quality') != analysis['max_quality']:
            enhanced_source['original_quality'] = source.get('quality', 'unknown')  # Remember original
            enhanced_source['quality'] = analysis['max_quality']
            logger.debug("Enhanced HLS source: %s -> %s",
                         source.get('quality', 'unknown'), enhanced_source['quality'])

    return enhanced_source


def enhance_sources_with_hls_quality(sources, session=None):
    """
    Enhance a list of sources by analyzing HLS quality for adaptive streams.

    Args:
        sources (list): List of source dictionaries
        session (requests.Session, optional): Session to use for requests

    Returns:
        list: Enhanced sources with HLS quality information
    """
    if not sources:
        return sources

    enhanced_sources = []

    for source in sources:
        enhanced_source = enhance_source_with_hls_quality(source, session)
        enhanced_sources.append(enhanced_source)

    return enhanced_sources


def _resolution_to_quality(_width, height):
    """Convert resolution to quality label."""
    if height >= 2160:
        return "2160p"
    if height >= 1440:
        return "1440p"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 480:
        return "480p"
    if height >= 360:
        return "360p"
    if height >= 240:
        return "240p"
    return "144p"


def _bandwidth_to_quality(bandwidth):
    """
    Convert bandwidth to approximate quality label.
    These are rough estimates based on typical HLS encoding.
    """
    if bandwidth >= 8000000:  # 8 Mbps+
        return "1080p"
    if bandwidth >= 4000000:  # 4 Mbps+
        return "720p"
    if bandwidth >= 2000000:  # 2 Mbps+
        return "480p"
    if bandwidth >= 1000000:  # 1 Mbps+
        return "360p"
    if bandwidth >= 500000:   # 500 kbps+
        return "240p"
    return "144p"

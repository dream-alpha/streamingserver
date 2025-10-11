# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
DRM Utilities

This module provides utilities for detecting DRM protection in streaming content.
It analyzes HLS playlists and other streaming formats to identify various DRM schemes.
"""

from __future__ import annotations

import re
import requests
from debug import get_logger

logger = get_logger(__file__)


class DRMInfo:
    """Container for DRM protection information."""

    def __init__(self):
        self.is_protected = False
        self.drm_systems = []
        self.key_systems = []
        self.content_protection = []
        self.details = {}


def check_drm_protection(playlist_url: str, stream_type: str = "") -> DRMInfo:
    """
    Check if a playlist is DRM protected by analyzing the playlist content.

    Args:
        playlist_url: URL to the playlist (M3U8 or MPD)
        stream_type: Type of the stream ('hls' or 'dash')

    Returns:
        DRMInfo object containing protection status and details
    """
    try:
        # Download playlist content
        response = requests.get(playlist_url, timeout=10)
        response.raise_for_status()
        content = response.text.lower()

        # Initialize DRM info
        drm_info = DRMInfo()

        # Check DRM based on stream type
        if stream_type.upper() == 'HLS':
            drm_info = _check_hls_drm(content, drm_info)
        elif stream_type.upper() == 'DASH':
            drm_info = _check_dash_drm(content, drm_info)
        else:
            # Unknown stream type - try both methods
            drm_info = _check_hls_drm(content, drm_info)
            drm_info = _check_dash_drm(content, drm_info)

        logger.info(f"DRM check for {playlist_url} ({stream_type}): Protected={drm_info.is_protected}, Systems={drm_info.drm_systems}")

        return drm_info

    except Exception as e:
        logger.error(f"Error checking DRM protection: {e}")
        # Return unknown status on error
        drm_info = DRMInfo()
        drm_info.is_protected = None
        return drm_info


def _check_hls_drm(content: str, drm_info: DRMInfo) -> DRMInfo:
    """Check for DRM protection in HLS playlists."""
    logger.debug("Checking HLS playlist for DRM protection")

    # Common HLS DRM indicators
    drm_patterns = {
        "AES-128": r"#EXT-X-KEY:METHOD=AES-128",
        "SAMPLE-AES": r"#EXT-X-KEY:METHOD=SAMPLE-AES",
        "AES-CTR": r"#EXT-X-KEY:METHOD=AES-CTR",
        "Widevine": r"urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",
        "PlayReady": r"urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95",
        "FairPlay": r"urn:uuid:94ce86fb-07ff-4f43-adb8-93d2fa968ca2",
        "ClearKey": r"urn:uuid:e2719d58-a985-b3c9-781a-b030af78d30e",
    }

    # Check for EXT-X-KEY tags (most common HLS DRM indicator)
    key_matches = re.findall(r"#EXT-X-KEY:([^\n\r]+)", content, re.IGNORECASE)
    if key_matches:
        drm_info.is_protected = True
        logger.info("Found %d EXT-X-KEY entries indicating DRM protection", len(key_matches))

        for key_line in key_matches:
            drm_info.details.setdefault("key_entries", []).append(key_line.strip())

            # Analyze key method
            method_match = re.search(r"METHOD=([^,\s]+)", key_line, re.IGNORECASE)
            if method_match:
                method = method_match.group(1)
                if method not in drm_info.drm_systems:
                    drm_info.drm_systems.append(method)
                logger.debug("Found DRM method: %s", method)

            # Check for key URI (indicates external key server)
            uri_match = re.search(r"URI=\"([^\"]+)\"", key_line, re.IGNORECASE)
            if uri_match:
                key_uri = uri_match.group(1)
                drm_info.details.setdefault("key_uris", []).append(key_uri)
                logger.debug("Found key URI: %s", key_uri[:50] + "..." if len(key_uri) > 50 else key_uri)

    # Check for additional DRM patterns
    for drm_name, pattern in drm_patterns.items():
        if re.search(pattern, content, re.IGNORECASE):
            drm_info.is_protected = True
            if drm_name not in drm_info.drm_systems:
                drm_info.drm_systems.append(drm_name)
            logger.debug("Found DRM system: %s", drm_name)

    # Check for session keys (another HLS DRM indicator)
    session_key_matches = re.findall(r"#EXT-X-SESSION-KEY:([^\n\r]+)", content, re.IGNORECASE)
    if session_key_matches:
        drm_info.is_protected = True
        drm_info.details["session_keys"] = session_key_matches
        logger.debug("Found %d session key entries", len(session_key_matches))

    return drm_info


def _check_dash_drm(content: str, drm_info: DRMInfo) -> DRMInfo:
    """Check for DRM protection in DASH manifests."""
    logger.debug("Checking DASH manifest for DRM protection")

    # Common DASH DRM patterns
    drm_patterns = {
        "Widevine": r"urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",
        "PlayReady": r"urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95",
        "ClearKey": r"urn:uuid:e2719d58-a985-b3c9-781a-b030af78d30e",
        "Adobe Access": r"urn:uuid:f239e769-efa3-4850-9c16-a903c6932efb",
    }

    # Check for ContentProtection elements
    protection_matches = re.findall(r"<ContentProtection[^>]*schemeIdUri=\"([^\"]+)\"", content, re.IGNORECASE)
    if protection_matches:
        drm_info.is_protected = True
        drm_info.content_protection = protection_matches
        logger.info("Found ContentProtection elements: %s", protection_matches)

    # Check for known DRM system UUIDs
    for drm_name, pattern in drm_patterns.items():
        if re.search(pattern, content, re.IGNORECASE):
            drm_info.is_protected = True
            if drm_name not in drm_info.drm_systems:
                drm_info.drm_systems.append(drm_name)
            logger.debug("Found DRM system: %s", drm_name)

    # Check for PSSH (Protection System Specific Header) boxes
    pssh_matches = re.findall(r"<cenc:pssh[^>]*>([^<]+)</cenc:pssh>", content, re.IGNORECASE)
    if pssh_matches:
        drm_info.is_protected = True
        drm_info.details["pssh_boxes"] = pssh_matches
        logger.debug("Found %d PSSH boxes", len(pssh_matches))

    return drm_info


def get_drm_summary(drm_info: DRMInfo) -> dict[str, any]:
    """
    Get a summary of DRM protection information.

    Args:
        drm_info (DRMInfo): DRM information object

    Returns:
        dict: Summary of DRM protection status
    """
    return {
        "is_protected": drm_info.is_protected,
        "drm_systems": drm_info.drm_systems,
        "key_systems": drm_info.key_systems,
        "content_protection": drm_info.content_protection,
        "protection_count": len(drm_info.drm_systems + drm_info.key_systems + drm_info.content_protection),
        "details_available": bool(drm_info.details)
    }


def is_stream_playable(drm_info: DRMInfo) -> tuple[bool, str]:
    """
    Determine if a stream is likely playable based on DRM protection.

    Args:
        drm_info (DRMInfo): DRM information object

    Returns:
        tuple[bool, str]: (is_playable, reason)
    """
    if not drm_info.is_protected:
        return True, "No DRM protection detected"

    # Check for potentially playable DRM systems
    playable_systems = ("ClearKey", "AES-128")  # Systems that might be handled

    for system in drm_info.drm_systems:
        if system in playable_systems:
            return True, f"Uses {system} which might be playable"

    # Check for commercial DRM systems (usually not playable without proper licenses)
    commercial_systems = ("Widevine", "PlayReady", "FairPlay", "SAMPLE-AES")

    for system in drm_info.drm_systems:
        if system in commercial_systems:
            return False, f"Uses {system} which requires proper licensing"

    return False, "DRM protected but specific system unknown"

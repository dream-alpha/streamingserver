#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
DRM Detection Utilities

Centralized DRM detection logic for streaming content. This module provides
functions to detect various DRM protection schemes in URLs, manifests, and
HTTP responses.
"""

from __future__ import annotations

import re
from typing import Any
from debug import get_logger

logger = get_logger(__file__)

# DRM detection patterns
DRM_PATTERNS = {
    "widevine": [
        r"widevine",
        r"drm\.widevine",
        r"wv-keyos",
        r"application/dash\+xml.*widevine",
    ],
    "playready": [
        r"playready",
        r"microsoft\.playready",
        r"mspr-2\.0",
        r"application/dash\+xml.*playready",
    ],
    "fairplay": [
        r"fairplay",
        r"fps-",
        r"application/vnd\.apple\.fps",
        r"skd://",
    ],
    "clearkey": [
        r"clearkey",
        r"clear-key",
        r"org\.w3\.clearkey",
    ],
    "generic_drm": [
        r"encrypted",
        r"protection",
        r"contentprotection",
        r"keyid",
        r"key_id",
        r"cenc",
        r"cbcs",
        r"#EXT-X-KEY.*METHOD=(?!NONE)(?!AES-128)",  # HLS encryption (not NONE or AES-128)
    ]
}

# Common DRM-related HTTP response indicators
DRM_HTTP_INDICATORS = [
    "x-drm-",
    "x-widevine-",
    "x-playready-",
    "content-protection",
    "www-authenticate",
    "authorization",
]

# Common DRM-related error messages
DRM_ERROR_MESSAGES = [
    "drm_protected",
    "encrypted",
    "protection",
    "license",
    "authorization",
    "forbidden",
    "content protection",
    "digital rights",
    "access denied",
    "subscription required",
    "geo-blocked",
    "not available in your region",
]


def is_public_aes128_encryption(key_line: str, content: str) -> bool:
    """
    Determine if an EXT-X-KEY line represents public AES-128 encryption (not DRM).

    Args:
        key_line (str): The EXT-X-KEY line from the playlist
        content (str): Full playlist content for context

    Returns:
        bool: True if this is public AES-128 encryption, False if it's likely DRM
    """
    key_line_upper = key_line.upper()
    content_lower = content.lower()

    # Check if this is AES-128 method
    if "METHOD=AES-128" not in key_line_upper:
        return False

    # Indicators that suggest public AES-128 (not DRM):

    # 1. PlutoTV/SamsungTV patterns (known to use public keys)
    pluto_patterns = [
        "pluto.tv", "plutotv", "samsung", "samsungtv"
    ]

    # 2. Public key server patterns (accessible HTTP URLs)
    public_key_patterns = [
        "http://", "https://"
    ]

    # 3. Simple key filenames (not complex DRM key servers)
    simple_key_patterns = [
        r"\.key$", r"key\d*\.bin$", r"encryption\.key$"
    ]

    # Check for PlutoTV/Samsung patterns in content or key URI
    for pattern in pluto_patterns:
        if pattern in content_lower or pattern in key_line.lower():
            return True

    # Extract URI from key line
    uri_match = re.search(r'URI="([^"]+)"', key_line)
    if uri_match:
        key_uri = uri_match.group(1).lower()

        # Check for public HTTP key servers (not complex DRM endpoints)
        if any(pattern in key_uri for pattern in public_key_patterns):
            # Further check if this looks like a simple key file
            if any(re.search(pattern, key_uri) for pattern in simple_key_patterns):
                return True

            # Check for known public streaming services
            known_public_services = [
                "pluto.tv", "samsung", "tubi", "crackle", "xumo"
            ]
            if any(service in key_uri for service in known_public_services):
                return True

    # Default to treating as potential DRM if we can't determine it's public
    return False


def detect_drm_in_url(url: str) -> dict[str, Any]:
    """
    Detect DRM indicators in a URL.

    Args:
        url (str): URL to check for DRM indicators

    Returns:
        dict: DRM detection result with 'has_drm', 'drm_type', and 'indicators'
    """
    if not url:
        return {"has_drm": False, "drm_type": None, "indicators": []}

    url_lower = url.lower()
    indicators = []
    drm_types = []

    # Check for specific DRM types
    for drm_type, patterns in DRM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower, re.IGNORECASE):
                drm_types.append(drm_type)
                indicators.append(f"URL pattern: {pattern}")
                break

    # Remove duplicates
    drm_types = list(set(drm_types))

    return {
        "has_drm": len(indicators) > 0,
        "drm_type": drm_types[0] if drm_types else None,
        "drm_types": drm_types,
        "indicators": indicators,
    }


def detect_drm_in_content(content: str, content_type: str = "") -> dict[str, Any]:
    """
    Detect DRM indicators in content (manifest, HTML, JSON, etc.).

    Args:
        content (str): Content to analyze
        content_type (str): Content type hint (e.g., "m3u8", "mpd", "html")

    Returns:
        dict: DRM detection result with 'has_drm', 'drm_type', and 'indicators'
    """
    if not content:
        return {"has_drm": False, "drm_type": None, "indicators": []}

    content_lower = content.lower()
    indicators = []
    drm_types = []

    # Check for specific DRM types
    for drm_type, patterns in DRM_PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(pattern, content_lower, re.IGNORECASE)
            if matches:
                drm_types.append(drm_type)
                indicators.append(f"Content pattern: {pattern} ({len(matches)} matches)")

    # Special handling for HLS manifests
    if content_type.lower() in {"m3u8", "hls"} or "#EXTM3U" in content:
        # Look for EXT-X-KEY with METHOD other than NONE
        key_lines = re.findall(r'#EXT-X-KEY:.*', content, re.IGNORECASE)
        for key_line in key_lines:
            if "METHOD=NONE" not in key_line.upper():
                # Check if this is standard AES-128 with public keys (not DRM)
                if is_public_aes128_encryption(key_line, content):
                    # This is standard AES-128 encryption with public keys, not DRM
                    continue
                drm_types.append("hls_encryption")
                indicators.append(f"HLS encryption: {key_line}")

    # Special handling for DASH manifests
    if content_type.lower() in {"mpd", "dash"} or "xmlns" in content_lower and "dash" in content_lower:
        # Look for ContentProtection elements
        protection_matches = re.findall(r'<contentprotection[^>]*>', content_lower, re.IGNORECASE)
        if protection_matches:
            drm_types.append("dash_protection")
            indicators.append(f"DASH ContentProtection elements found ({len(protection_matches)})")

    # Remove duplicates
    drm_types = list(set(drm_types))

    return {
        "has_drm": len(indicators) > 0,
        "drm_type": drm_types[0] if drm_types else None,
        "drm_types": drm_types,
        "indicators": indicators,
    }


def detect_drm_in_headers(headers: dict[str, str]) -> dict[str, Any]:
    """
    Detect DRM indicators in HTTP headers.

    Args:
        headers (dict): HTTP headers to analyze

    Returns:
        dict: DRM detection result with 'has_drm', 'drm_type', and 'indicators'
    """
    if not headers:
        return {"has_drm": False, "drm_type": None, "indicators": []}

    indicators = []
    drm_types = []

    # Check header names and values
    for header_name, header_value in headers.items():
        header_name_lower = header_name.lower()
        header_value_lower = str(header_value).lower()

        # Check for DRM-related header names
        for drm_indicator in DRM_HTTP_INDICATORS:
            if drm_indicator in header_name_lower:
                indicators.append(f"Header name: {header_name}")
                if "widevine" in header_name_lower:
                    drm_types.append("widevine")
                elif "playready" in header_name_lower:
                    drm_types.append("playready")
                else:
                    drm_types.append("generic_drm")

        # Check for DRM patterns in header values
        for drm_type, patterns in DRM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, header_value_lower, re.IGNORECASE):
                    drm_types.append(drm_type)
                    indicators.append(f"Header value ({header_name}): {pattern}")

    # Remove duplicates
    drm_types = list(set(drm_types))

    return {
        "has_drm": len(indicators) > 0,
        "drm_type": drm_types[0] if drm_types else None,
        "drm_types": drm_types,
        "indicators": indicators,
    }


def detect_drm_in_error(error_message: str) -> dict[str, Any]:
    """
    Detect DRM-related errors in error messages.

    Args:
        error_message (str): Error message to analyze

    Returns:
        dict: DRM detection result with 'has_drm', 'drm_type', and 'indicators'
    """
    if not error_message:
        return {"has_drm": False, "drm_type": None, "indicators": []}

    error_lower = error_message.lower()
    indicators = []

    # Check for DRM-related error messages
    for drm_keyword in DRM_ERROR_MESSAGES:
        if drm_keyword in error_lower:
            indicators.append(f"Error keyword: {drm_keyword}")

    return {
        "has_drm": len(indicators) > 0,
        "drm_type": "error_based" if indicators else None,
        "drm_types": ["error_based"] if indicators else [],
        "indicators": indicators,
    }


def comprehensive_drm_check(url: str = "", content: str = "",
                            headers: dict[str, str] = None,
                            error_message: str = "",
                            content_type: str = "") -> dict[str, Any]:
    """
    Perform comprehensive DRM detection across all available sources.

    Args:
        url (str): URL to check
        content (str): Content to analyze (manifest, HTML, etc.)
        headers (dict): HTTP headers to analyze
        error_message (str): Error message to analyze
        content_type (str): Content type hint

    Returns:
        dict: Comprehensive DRM detection result
    """
    results = {}
    all_indicators = []
    all_drm_types = []

    # Check URL
    if url:
        url_result = detect_drm_in_url(url)
        results["url"] = url_result
        if url_result["has_drm"]:
            all_indicators.extend(url_result["indicators"])
            all_drm_types.extend(url_result["drm_types"])

    # Check content
    if content:
        content_result = detect_drm_in_content(content, content_type)
        results["content"] = content_result
        if content_result["has_drm"]:
            all_indicators.extend(content_result["indicators"])
            all_drm_types.extend(content_result["drm_types"])

    # Check headers
    if headers:
        headers_result = detect_drm_in_headers(headers)
        results["headers"] = headers_result
        if headers_result["has_drm"]:
            all_indicators.extend(headers_result["indicators"])
            all_drm_types.extend(headers_result["drm_types"])

    # Check error message
    if error_message:
        error_result = detect_drm_in_error(error_message)
        results["error"] = error_result
        if error_result["has_drm"]:
            all_indicators.extend(error_result["indicators"])
            all_drm_types.extend(error_result["drm_types"])

    # Remove duplicates
    all_drm_types = list(set(all_drm_types))

    # Overall result
    has_drm = len(all_indicators) > 0
    primary_drm_type = all_drm_types[0] if all_drm_types else None

    logger.debug("DRM check - URL: %s, Content length: %d, Has DRM: %s, Type: %s",
                 url[:50] + "..." if len(url) > 50 else url,
                 len(content) if content else 0,
                 has_drm, primary_drm_type)

    return {
        "has_drm": has_drm,
        "drm_type": primary_drm_type,
        "drm_types": all_drm_types,
        "indicators": all_indicators,
        "details": results,
        "confidence": "high" if len(all_indicators) >= 2 else "medium" if all_indicators else "low"
    }


def is_drm_protected(url: str = "", content: str = "",
                     headers: dict[str, str] = None,
                     error_message: str = "",
                     content_type: str = "") -> bool:
    """
    Simple boolean check for DRM protection.

    Args:
        url (str): URL to check
        content (str): Content to analyze
        headers (dict): HTTP headers to analyze
        error_message (str): Error message to analyze
        content_type (str): Content type hint

    Returns:
        bool: True if DRM is detected, False otherwise
    """
    result = comprehensive_drm_check(url, content, headers, error_message, content_type)
    return result["has_drm"]


def get_drm_type(url: str = "", content: str = "",
                 headers: dict[str, str] = None,
                 error_message: str = "",
                 content_type: str = "") -> str | None:
    """
    Get the primary DRM type detected.

    Args:
        url (str): URL to check
        content (str): Content to analyze
        headers (dict): HTTP headers to analyze
        error_message (str): Error message to analyze
        content_type (str): Content type hint

    Returns:
        str|None: Primary DRM type or None if no DRM detected
    """
    result = comprehensive_drm_check(url, content, headers, error_message, content_type)
    return result["drm_type"]

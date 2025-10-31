# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import re


def format_size(size_bytes):
    """Format bytes as human-readable size"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def clean_text(text: str) -> str:
    """Clean up text by removing HTML tags and normalizing whitespace.

    Args:
        text: Input text that may contain HTML tags and entities

    Returns:
        Cleaned text with HTML tags removed and whitespace normalized
    """
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = (
        text.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_for_json(text: str) -> str:
    """Sanitize strings to prevent JSON serialization issues.

    Removes control characters and invalid unicode while preserving
    printable ASCII and extended unicode characters.

    Args:
        text: Input text that may contain control characters

    Returns:
        Sanitized text safe for JSON serialization
    """
    if not text:
        return ""
    return re.sub(r'[^\x20-\x7E\u00A0-\uFFFF]', '', text)

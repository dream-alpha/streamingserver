#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Base Provider Class

This module contains the base class that all provider implementations inherit from.
It provides common interface and utility methods.
"""

from __future__ import annotations

import re
import gzip
import datetime
from session_utils import get_session
from auth_utils import get_headers
from debug import get_logger

try:
    import brotli
except ImportError:
    brotli = None

logger = get_logger(__file__)


class BaseProvider:
    """Base class for provider implementations with common utilities"""

    def __init__(self, args: dict):
        self.provider_id = args.get("provider_id", "")
        self.data_dir = args.get("data_dir")  # Already a Path object from socket_request_handler
        self.title = ""
        self.base_url = ""
        self.description = ""

        # Create session for HTTP requests
        self.session = get_session()

    def get_categories(self) -> list[dict[str, str]]:
        """Get site categories"""
        raise NotImplementedError

    def get_media_items(self, category: dict, _page: int = 1, _limit: int = 28) -> list[dict]:
        """Get media items for a category"""
        raise NotImplementedError

    def parse_iso8601(self, dtstr):
        """
        Parse ISO 8601 datetime string using Python 3's built-in fromisoformat.
        Returns datetime object or None if parsing fails.
        """
        try:
            # Handle timezone info by removing 'Z' suffix if present
            if dtstr.endswith('Z'):
                dtstr = dtstr[:-1] + '+00:00'
            return datetime.datetime.fromisoformat(dtstr)
        except (ValueError, AttributeError):
            return None

    def get_standard_headers(self, purpose: str = "general") -> dict[str, str]:
        """Get standardized headers for HTTP requests"""
        base_headers = get_headers("browser")

        if purpose in {"thumbnail", "metadata", "scraping"}:
            base_headers["Referer"] = self.base_url

        if purpose == "general":
            base_headers.update({
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            })

        if purpose == "scraping":
            base_headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Cache-Control": "no-cache",
            })

        return base_headers

    def get_response_text(self, response) -> str:
        """Get properly decoded text from HTTP response, handling compression"""
        try:
            # Ensure proper encoding
            response.encoding = response.apparent_encoding or 'utf-8'
            html = response.text

            # Check if we got binary data instead of text (indicates decompression issue)
            if any(ord(char) < 32 and char not in '\t\n\r' for char in html[:100]):
                logger.warning("Received binary data - attempting manual decompression")

                # Try manual decompression
                raw_content = response.content

                try:
                    # Try Brotli decompression first (most common on modern sites)
                    if brotli:
                        decompressed = brotli.decompress(raw_content)
                        html = decompressed.decode('utf-8', errors='ignore')
                        logger.info("Successfully decompressed Brotli content")
                    else:
                        raise ImportError("brotli not available")
                except Exception:
                    try:
                        # Try gzip decompression
                        decompressed = gzip.decompress(raw_content)
                        html = decompressed.decode('utf-8', errors='ignore')
                        logger.info("Successfully decompressed gzipped content")
                    except Exception:
                        # Fall back to raw content with UTF-8 decoding
                        html = raw_content.decode('utf-8', errors='ignore')
                        logger.info("Used raw content with UTF-8 decoding")

            return html

        except Exception as e:
            logger.error("Error decoding response: %s", e)
            return ""

    def extract_video_id(self, url: str) -> str:
        """Extract video ID from URL - should be overridden by providers"""
        if not url:
            return "unknown"

        # Generic patterns that might work for many sites
        patterns = [
            r'/videos/([^/?]+)',
            r'/v/([^/?]+)',
            r'/(\d+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return "unknown"

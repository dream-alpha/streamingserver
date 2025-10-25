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
from typing import Any
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

    def __init__(self, provider_id=None, data_dir=None):
        self.provider_id = provider_id or ""
        self.data_dir = data_dir
        self.name = ""
        self.title = ""
        self.base_url = ""
        self.description = ""
        self.supports_categories = False
        self.supports_search = False

        # Create session for HTTP requests
        self.session = get_session()
        self.session.headers.update(get_headers("browser"))

    def get_categories(self) -> list[dict[str, str]]:
        """Get site categories"""
        raise NotImplementedError

    def get_latest_videos(self, page: int = 1, limit: int = 28) -> dict[str, Any]:
        """Get latest videos"""
        raise NotImplementedError

    def search_videos(
        self, term: str, page: int = 1, limit: int = 28
    ) -> dict[str, Any]:
        """Search for videos"""
        raise NotImplementedError

    def resolve_video_url(self, video_url: str) -> dict[str, Any]:
        """Resolve video page to streaming URLs"""
        raise NotImplementedError

    def resolve_url(self, url: str, quality: str = "best") -> str:
        """
        Resolve video page URL to streaming URL with quality selection
        Called by socket server for recording
        """

        # Check if this is a search URL - these cannot be resolved to video streams
        if "/search" in url or "/search-video/" in url:
            logger.error("Cannot resolve search URL to video stream: %s", url)
            logger.error("Search URLs cannot be used for recording - need individual video page URLs")
            return url  # Return original so error handling upstream can catch this

        try:
            # Use the existing resolve_video_url method
            result = self.resolve_video_url(url)

            if result and result.get("resolved") and result.get("video_urls"):
                video_urls = result.get("video_urls", [])

                # Find requested quality or use best (first in list)
                for video_url_info in video_urls:
                    if quality == "best" or video_url_info.get("quality") == quality:
                        streaming_url = video_url_info.get("url", "")
                        if streaming_url:
                            logger.info("Selected %s quality for recording", video_url_info.get("quality", "unknown"))
                            return streaming_url

                # Fallback to first available URL
                if video_urls:
                    streaming_url = video_urls[0].get("url", "")
                    if streaming_url:
                        logger.info("Using fallback quality: %s", video_urls[0].get("quality", "unknown"))
                        return streaming_url

            logger.warning("No streaming URLs found, returning original URL")
            return url

        except Exception as e:
            logger.error("Error in resolve_url: %s", e)
            return url

    def _clean_text(self, text: str) -> str:
        """Clean up text by removing HTML tags and normalizing"""
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

    def sanitize_for_json(self, text: str) -> str:
        """Sanitize strings to prevent JSON serialization issues"""
        if not text:
            return ""
        return re.sub(r'[^\x20-\x7E\u00A0-\uFFFF]', '', text)

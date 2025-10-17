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
from typing import Any
from debug import get_logger

logger = get_logger(__file__)


class BaseProvider:
    """Base class for provider implementations"""

    def __init__(self, provider_id=None, data_dir=None):
        self.provider_id = provider_id or ""
        self.data_dir = data_dir or ""
        self.name = ""
        self.title = ""
        self.base_url = ""
        self.description = ""
        self.supports_categories = False
        self.supports_search = False

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

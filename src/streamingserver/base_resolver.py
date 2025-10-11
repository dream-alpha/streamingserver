#!/usr/bin/env python3
"""
Base Resolver Module

This module contains the BaseResolver class that provides common functionality
for all site-specific resolvers.
"""

from __future__ import annotations

from typing import Any

import requests
from debug import get_logger

logger = get_logger(__file__)


class BaseResolver:
    """Base class for site resolvers"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )

    def resolve_url(self, url: str, resolution: str = "best") -> str:
        """Resolve video page URL to streaming URL with quality selection"""
        try:
            resolution_result = self.resolve_urls(url)  # pylint: disable=assignment-from-no-return
        except Exception as e:
            logger.warning("Error during URL resolution: %s", e)
            return url

        if resolution_result is not None and resolution_result.get("resolved") and resolution_result.get("video_urls"):
            # Get the streaming URL based on quality preference
            video_urls = resolution_result.get("video_urls", [])
            url_to_record = url

            # If specific resolution requested, try to find it
            if resolution != "best":
                for video_url in video_urls:
                    if video_url.get("quality") == resolution:
                        url_to_record = video_url.get("url", url_to_record)
                        logger.info("Found %s quality URL", resolution)
                        break

            # Fallback to best quality (usually first in the list)
            if url_to_record == url and video_urls:
                url_to_record = video_urls[0].get("url", url_to_record)
                logger.info("Using best quality URL")

            logger.info("Resolved to streaming URL: %s", url_to_record[:100] + "..." if len(url_to_record) > 100 else url_to_record)
            return url_to_record

        logger.warning("Resolution failed, using original URL")
        return url

    def resolve_urls(self, url: str) -> dict[str, Any] | None:  # pylint: disable=unused-argument
        """Base method to be overridden by specific resolvers"""
        logger.warning("resolve_urls not implemented in base class")

    def can_resolve(self, url: str) -> bool:  # pylint: disable=unused-argument
        """Check if this resolver can handle the URL"""
        # Base implementation always returns False - override in subclasses
        return False

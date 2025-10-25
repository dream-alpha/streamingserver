#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
SamsungTV Resolver Implementation

This module contains a simple passthrough resolver for SamsungTV that returns
the incoming URL unchanged in the standard dictionary format.
"""

from __future__ import annotations

from typing import Any
from base_resolver import BaseResolver
from debug import get_logger

logger = get_logger(__file__)


class Resolver(BaseResolver):
    """SamsungTV passthrough resolver - returns URLs unchanged"""

    def __init__(self):
        super().__init__()
        self.name = "samsungtv"

    def resolve_url(self, args: dict) -> dict[str, Any] | None:
        """
        Passthrough resolver with DRM detection - returns the URL unchanged or DRM info.

        Args:
            args (dict): Input arguments containing the URL and potentially video metadata

        Returns:
            dict: Standard resolver response with unchanged URL or DRM info if DRM detected
        """
        url = args.get("url", "")

        # Debug: Log all args to understand the data structure
        logger.info("SamsungTV resolver called with args: %s", args)

        # Check for DRM protection in video metadata
        # The video metadata might contain license_url indicating DRM content
        video_data = args.get("video", {}) or args
        if "license_url" in video_data:
            license_url = video_data["license_url"]
            logger.info("SamsungTV DRM detected - license_url: %s", license_url)

            # Return DRM result instead of raising exception
            return {
                "resolved_url": url,
                "auth_tokens": None,
                "session": self.session,
                "resolved": False,  # Mark as not resolved due to DRM
                "resolver": self.name,
                "recorder_id": "unknown",
                "drm_protected": True,
                "drm_info": f"Samsung TV content with Widevine DRM (license: {license_url})",
                "error_id": "drm_protected",
                "error_msg": f"DRM Protected Stream: Samsung TV content with Widevine DRM (license: {license_url})"
            }

        logger.info("SamsungTV resolver - returning URL: %s", url)

        return {
            "resolved_url": url,
            "auth_tokens": None,  # No authentication needed for SamsungTV
            "session": self.session,  # Pass resolver's session to recorder
            "resolved": True,
            "resolver": self.name,
            "recorder_id": "hls_live",
        }

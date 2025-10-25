#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
PlutoTV Resolver Implementation
"""

from __future__ import annotations

import re
from typing import Any
from base_resolver import BaseResolver
from debug import get_logger

logger = get_logger(__file__)


class Resolver(BaseResolver):

    def __init__(self):
        super().__init__()
        self.name = "plutotv"

    def resolve_url(self, args) -> dict[str, Any] | None:
        """
        Url resolver

        Args:
            args (dict): Input arguments containing the URL and others

        Returns:
            dict: Standard resolver response
        """
        url = args.get("url", "")
        show_ads = args.get("show_ads", True)
        logger.debug("PlutoTV Resolver called with URL: %s, show_ads: %s", url, show_ads)
        if not show_ads:
            channel_id = ""
            if url.startswith("http"):
                # Extract channel_id from url using regex
                match = re.search(r"/channel/([^/]+)/", url)
                if match:
                    channel_id = match.group(1)
                    logger.debug("Extracted channel_id: %s", channel_id)
            else:
                channel_id = url
            if channel_id:
                url = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{channel_id}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="
        logger.info("Resolved PlutoTV URL: %s", url)
        return {
            "resolved_url": url,
            "auth_tokens": None,  # No authentication needed for PlutoTV
            "session": self.session,  # Pass resolver's session to recorder
            "resolved": True,
            "resolver": self.name,
            "recorder_id": "hls_live",
        }

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

    def resolve_url(self) -> dict[str, Any] | None:
        """
        Url resolver

        Returns:
            dict: Standard resolver response
        """
        resolved_url = self.url
        logger.debug("PlutoTV Resolver called with URL: %s, self.show_ads: %s", self.url, self.show_ads)
        if not self.show_ads:
            channel_id = ""
            if self.url.startswith("http"):
                # Extract channel_id from url using regex
                match = re.search(r"/channel/([^/]+)/", self.url)
                if match:
                    channel_id = match.group(1)
                    logger.debug("Extracted channel_id: %s", channel_id)
            else:
                channel_id = self.url
            if channel_id:
                resolved_url = f"http://stitcher-ipv4.pluto.tv/v1/stitch/embed/hls/channel/{channel_id}/master.m3u8?deviceType=unknown&deviceMake=unknown&deviceModel=unknown&deviceVersion=unknown&appVersion=unknown&deviceLat=90&deviceLon=0&deviceDNT=TARGETOPT&deviceId=PSID&advertisingId=PSID&us_privacy=1YNY&profileLimit=&profileFloor=&embedPartner="
        logger.info("Resolved PlutoTV URL: %s", resolved_url)
        self.resolve_result.update({
            "resolved_url": resolved_url,
            "session": self.session,
            "recorder_id": "hls_live",
        })
        return self.resolve_result

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
        self.domains = ["samsungtv.com", "samsung.com"]

    def can_resolve(self, _url: str) -> bool:
        """Check if this resolver can handle the URL"""
        # Accept any URL for SamsungTV (passthrough behavior)
        return True

    def resolve_url(self, args: dict) -> dict[str, Any] | None:
        """
        Passthrough resolver - returns the URL unchanged in standard dictionary format.

        Args:
            args (dict): Input arguments containing the URL

        Returns:
            dict: Standard resolver response with unchanged URL
        """
        url = args.get("url", "")
        logger.info("SamsungTV resolver - returning URL: %s", url)

        return {
            "resolved_url": url,
            "auth_tokens": None,  # No authentication needed for SamsungTV
            "session": self.session,  # Pass resolver's session to recorder
            "resolved": True,
            "resolver": "samsungtv",
            "recorder_id": "hls_live",
        }

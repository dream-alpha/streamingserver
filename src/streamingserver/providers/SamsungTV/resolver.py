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

    def resolve_url(self) -> dict[str, Any] | None:
        """
        Passthrough resolver - returns the URL unchanged in standard dictionary format.

        Returns:
            dict: Standard resolver response with URL unchanged
        """
        logger.info("SamsungTV resolver - returning URL: %s", self.url)

        self.resolve_result.update({
            "resolved_url": self.url,
            "session": self.session,
            "recorder_id": "hls_live",
        })
        return self.resolve_result

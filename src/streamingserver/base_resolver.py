#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Base Resolver Module

This module contains the BaseResolver class that provides common functionality
for all site-specific resolvers with centralized quality selection.
"""

from __future__ import annotations

import re
from typing import Any
import m3u8
from auth_utils import get_headers
from quality_utils import sort_sources_by_quality_and_codec, select_best_quality, DEFAULT_AVAILABLE_QUALITIES
from session_utils import get_session
from debug import get_logger

logger = get_logger(__file__)


class BaseResolver:
    """Base class for site resolvers"""

    def __init__(self):
        self.session = get_session()
        self.session.headers.update(get_headers("standard"))

    def resolve_url(self, url: str, quality: str = "best", av1: bool = None) -> str:
        """
        Resolve video page URL to streaming URL with centralized quality selection and template resolution.

        Args:
            url (str): Video page URL or template URL to resolve
            quality (str): Preferred video quality (e.g., "720p", "best", "fhd", "hd")
            av1 (bool): Whether to include AV1 codecs (None=use global setting, True=force enable, False=force disable)

        Returns:
            str: Direct streaming URL or empty string if resolution fails
        """
        # First, check if this is a template URL that can be resolved directly
        if self._is_template_url(url):
            logger.info("Detected template URL, attempting template resolution")
            resolved_template_url = self._resolve_template_url(url, quality)
            if resolved_template_url and resolved_template_url != url:
                logger.info("Template URL resolved: %s",
                            resolved_template_url[:100] + "..." if len(resolved_template_url) > 100 else resolved_template_url)
                return resolved_template_url
            logger.warning("Template resolution failed, falling back to standard resolution")

        # Standard resolution for non-template URLs or template fallback
        try:
            resolution_result = self.resolve_urls(url)  # pylint: disable=assignment-from-no-return
        except Exception as e:
            logger.warning("Error during URL resolution: %s", e)
            return url

        # Check for successful resolution with sources
        if resolution_result is not None and resolution_result.get("resolved"):
            sources = resolution_result.get("sources", [])

            # Also support legacy "video_urls" format for compatibility
            if not sources:
                sources = resolution_result.get("video_urls", [])

            if sources:
                # Use centralized quality selection
                optimal_url = self._select_optimal_source(sources, quality, av1)

                if optimal_url and optimal_url != url:
                    logger.info("Resolved to streaming URL: %s",
                                optimal_url[:100] + "..." if len(optimal_url) > 100 else optimal_url)
                    return optimal_url

        logger.warning("Resolution failed, using original URL")
        return url

    def _select_optimal_source(self, sources: list[dict[str, Any]], quality: str = "best", av1: bool = None) -> str:
        """
        Select optimal source URL from available sources using centralized quality selection.

        Args:
            sources (list): List of source dictionaries with url and quality fields
            quality (str): Preferred quality ("best", "720p", "fhd", "hd", etc.)
            av1 (bool): Whether to include AV1 codecs (None=use global setting, True=force enable, False=force disable)

        Returns:
            str: URL of optimal source or empty string if none found
        """
        if not sources:
            logger.warning("No sources available for quality selection")
            return ""

        # Sort sources by quality and codec preference (best first)
        sorted_sources = sort_sources_by_quality_and_codec(sources, "quality", "codec", av1)

        # If looking for best quality, return the first (highest quality) source
        if quality == "best":
            best_source = sorted_sources[0]
            logger.info("Selected best quality source: %s", best_source.get("quality", "unknown"))
            return best_source.get("url", "")

        # Look for exact quality match
        for source in sorted_sources:
            if source.get("quality") == quality:
                logger.info("Found exact quality match: %s", quality)
                return source.get("url", "")

        # Extract available qualities for intelligent selection
        available_qualities = [source.get("quality", "unknown") for source in sources]
        selected_quality = select_best_quality(available_qualities, quality)

        # Find source with selected quality
        for source in sorted_sources:
            if source.get("quality") == selected_quality:
                logger.info("Selected closest quality match: %s (target: %s)",
                            selected_quality, quality)
                return source.get("url", "")

        # Fallback to best available source
        if sorted_sources:
            fallback_source = sorted_sources[0]
            logger.info("Using fallback quality: %s", fallback_source.get("quality", "unknown"))
            return fallback_source.get("url", "")

        return ""

    def _is_template_url(self, url: str) -> bool:
        """
        Check if URL contains template patterns that need resolution.

        Args:
            url (str): URL to check

        Returns:
            bool: True if URL contains template patterns
        """
        template_patterns = [
            r"_TPL_",                                         # xHamster-style templates
            r"\$(RepresentationID|Time|Number|Bandwidth)\$",  # DASH-style templates
            r"\{[^}]*\}",                                     # Generic placeholder templates
        ]

        for pattern in template_patterns:
            if re.search(pattern, url):
                logger.debug("Found template pattern '%s' in URL", pattern)
                return True

        return False

    def _resolve_template_url(self, url: str, quality: str = "best") -> str:
        """
        Resolve template URL by substituting variables with appropriate values.

        Args:
            url (str): Template URL to resolve
            quality (str): Preferred quality for template resolution

        Returns:
            str: Resolved URL or original URL if resolution fails
        """
        logger.info("Resolving template URL: %s", url)

        # Handle _TPL_ placeholder (xHamster style)
        if "_TPL_" in url:
            return self._resolve_tpl_template(url, quality)

        # Handle DASH-style templates
        dash_patterns = [r"\$RepresentationID\$", r"\$Time\$", r"\$Number\$", r"\$Bandwidth\$"]
        if any(re.search(pattern, url) for pattern in dash_patterns):
            return self._resolve_dash_template(url, quality)

        # Handle generic placeholder templates
        if re.search(r"\{[^}]*\}", url):
            logger.warning("Generic placeholder templates not yet implemented")
            return url

        return url

    def _resolve_tpl_template(self, url: str, quality: str = "best") -> str:
        """
        Resolve _TPL_ template URLs (xHamster style).

        Args:
            url (str): URL with _TPL_ placeholder
            quality (str): Preferred quality

        Returns:
            str: URL with _TPL_ replaced with appropriate quality
        """
        try:
            # Extract available qualities from the template playlist
            available_qualities = self._extract_qualities_from_template_playlist(url)

            if not available_qualities:
                # Try to parse qualities from URL parameters as fallback
                available_qualities = self._parse_qualities_from_url_params(url)

            if not available_qualities:
                logger.warning("No qualities found, using defaults")
                available_qualities = DEFAULT_AVAILABLE_QUALITIES

            # Select best quality based on preference
            selected_quality = select_best_quality(available_qualities, quality)
            logger.info("Selected quality for _TPL_ resolution: %s", selected_quality)

            # Replace _TPL_ with selected quality
            resolved_url = url.replace("_TPL_", selected_quality)
            return resolved_url

        except Exception as e:
            logger.error("Failed to resolve _TPL_ template: %s", e)
            return url

    def _resolve_dash_template(self, url: str, quality: str = "best") -> str:  # pylint: disable=unused-argument
        """
        Resolve DASH-style template URLs ($RepresentationID$, etc.).

        Args:
            url (str): URL with DASH-style placeholders
            quality (str): Preferred quality (unused for now)

        Returns:
            str: URL with placeholders resolved (basic implementation)
        """
        resolved_url = url

        # Basic template resolution - in practice, these would come from MPD/context
        template_replacements = {
            r"\$RepresentationID\$": "video_1",  # Would come from MPD
            r"\$Time\$": "0",                    # Would be calculated based on timing
            r"\$Number\$": "1",                  # Would be segment number
            r"\$Bandwidth\$": "1000000",         # Would come from selected representation
        }

        for pattern, replacement in template_replacements.items():
            if re.search(pattern, resolved_url):
                resolved_url = re.sub(pattern, replacement, resolved_url)
                logger.debug("Replaced template variable %s with %s", pattern, replacement)

        if resolved_url != url:
            logger.info("Resolved DASH template: %s -> %s", url[:80], resolved_url[:80])

        return resolved_url

    def _extract_qualities_from_template_playlist(self, template_url: str) -> list[str]:
        """
        Extract available qualities from a template playlist.

        Args:
            template_url (str): Template URL to analyze

        Returns:
            list[str]: List of available qualities
        """
        try:
            session = get_session()
            response = session.get(template_url, timeout=10)
            response.raise_for_status()

            playlist_content = response.text
            logger.debug("Loaded template playlist content (%d bytes)", len(playlist_content))

            # Parse the master playlist using m3u8 library
            master_playlist = m3u8.loads(playlist_content)

            if master_playlist.playlists:
                qualities = []

                for variant in master_playlist.playlists:
                    if variant.stream_info and variant.stream_info.resolution:
                        # Extract quality from resolution (e.g., 1280x720 -> 720p)
                        _, height = variant.stream_info.resolution
                        quality = f"{height}p"
                        qualities.append(quality)

                        logger.debug("Found variant: %s (%s, %d bps)",
                                     quality, variant.stream_info.resolution,
                                     variant.stream_info.bandwidth or 0)

                if qualities:
                    logger.info("Extracted qualities from template playlist: %s", qualities)
                    return qualities

        except Exception as e:
            logger.debug("Failed to extract qualities from template playlist: %s", e)

        return []

    def _parse_qualities_from_url_params(self, url: str) -> list[str]:
        """
        Parse available qualities from URL parameters (xHamster style).

        Args:
            url (str): URL to parse

        Returns:
            list[str]: List of available qualities
        """
        # Parse available qualities from the URL
        # Example: multi=256x144:144p:,426x240:240p:,854x480:480p:

        multi_pattern = r'multi=([^/&]+)'
        multi_match = re.search(multi_pattern, url)

        if multi_match:
            multi_string = multi_match.group(1)
            # Extract quality info: resolution:quality:
            quality_pattern = r'(\d+x\d+):(\d+p):'
            quality_matches = re.findall(quality_pattern, multi_string)

            qualities = [quality for _resolution, quality in quality_matches]

            if qualities:
                logger.info("Parsed qualities from URL parameters: %s", qualities)
                return qualities

        return []

    def resolve_urls(self, url: str) -> dict[str, Any] | None:  # pylint: disable=unused-argument
        """
        Base method to be overridden by specific resolvers.

        Should return a dictionary with:
        - resolved: bool (True if successful)
        - sources: list of dicts with 'url' and 'quality' fields
        - resolver: str (resolver name for debugging)
        """
        logger.warning("resolve_urls not implemented in base class")

    def can_resolve(self, url: str) -> bool:  # pylint: disable=unused-argument
        """Check if this resolver can handle the URL"""
        # Base implementation always returns False - override in subclasses
        return False

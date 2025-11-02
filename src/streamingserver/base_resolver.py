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
import m3u8
from auth_utils import get_headers
from quality_utils import select_best_source
from session_utils import get_session
from debug import get_logger

logger = get_logger(__file__)


class BaseResolver:
    """Base class for site resolvers"""

    def __init__(self, args: dict):
        self.provider_id = args.get("provider_id", "")
        self.data_dir = args.get("data_dir")
        self.url = args.get("url", "")
        self.quality = args.get("quality", "best")
        self.av1 = args.get("av1", False)
        self.show_ads = args.get("show_ads", True)  # PlutoTV specific
        self.session = get_session()
        self.session.headers.update(get_headers("standard"))
        self.resolve_result = args.copy()

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
            # Try fetching a sample playlist to extract available qualities
            available_qualities = []

            # Subclasses (like xHamster) may override this to parse URL params first
            if hasattr(self, '_parse_qualities_from_url_params'):
                logger.debug("Found _parse_qualities_from_url_params method, calling it")
                available_qualities = self._parse_qualities_from_url_params(url)
                logger.debug("_parse_qualities_from_url_params returned: %s", available_qualities)
            else:
                logger.debug("No _parse_qualities_from_url_params method found in resolver")

            # If URL params don't contain quality info, try fetching a sample playlist
            if not available_qualities:
                # Replace _TPL_ with a common quality to fetch the master playlist
                sample_url = url.replace("_TPL_", "720p")
                available_qualities = self._extract_qualities_from_template_playlist(sample_url)

            if not available_qualities:
                # Can't determine available qualities - just replace with requested quality
                # If it doesn't exist, the recorder will fail naturally with proper error handling
                logger.warning("Could not determine available qualities from template URL")
                logger.info("Replacing _TPL_ with requested quality: %s", quality)

                # Normalize quality format (add 'p' suffix if missing)
                if quality and quality not in {"best", "adaptive"} and not quality.endswith('p'):
                    target_quality = f"{quality}p"
                elif quality in {"best", "adaptive"}:
                    target_quality = "720p"
                else:
                    target_quality = quality

                return url.replace("_TPL_", target_quality)

            # Create source objects for each available quality - same as provider resolvers
            sources = []
            for qual in available_qualities:
                resolved_url = url.replace("_TPL_", qual)
                sources.append({
                    "url": resolved_url,
                    "quality": qual,
                    "format": "m3u8"  # Template URLs are typically HLS
                })

            # Use the same selection logic as providers
            best_source = select_best_source(sources, quality, codec_aware=False)
            if best_source:
                logger.info("Selected quality for _TPL_ resolution: %s", best_source.get("quality"))
                return best_source.get("url")

            # Shouldn't reach here, but fallback to first available quality
            logger.warning("select_best_source returned None, using first available quality")
            return url.replace("_TPL_", available_qualities[0])

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
            logger.info("Resolved DASH template: %s -> %s", url, resolved_url)

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

    def determine_recorder_id(self, url: str) -> str:
        """
        Determine the appropriate recorder type based on URL characteristics.

        This is a centralized implementation that works for most providers.
        Providers can override this method if they need custom logic.

        Args:
            url (str): The resolved streaming URL

        Returns:
            str: One of 'mp4', 'hls_basic', 'hls_live', 'hls_m4s'
        """
        url_lower = url.lower()

        # Check for HLS formats
        if '.m3u8' in url_lower:
            # Check for MP4/M4S segment-based HLS streams
            if 'm4s' in url_lower or '.mp4.' in url_lower:
                return 'hls_m4s'
            # Default HLS type
            return 'hls_basic'

        # Default to MP4 for direct video files
        return 'mp4'

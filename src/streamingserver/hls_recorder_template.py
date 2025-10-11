# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Template HLS Recorder

This module handles template-based HLS streams that use URL templates
with variables like $RepresentationID$, $Time$, $Number$, etc.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, parse_qs
import m3u8
from hls_recorder_base import HLS_Recorder_Base
from hls_recorder_m4s import HLS_Recorder_M4S
from session_utils import get_session
from debug import get_logger

logger = get_logger(__file__)


class HLS_Recorder_Template(HLS_Recorder_Base):
    """
    Recorder for template-based HLS streams.

    Template-based HLS streams use URL patterns with variables that need
    to be resolved to actual segment URLs.
    """

    def __init__(self):
        super().__init__()
        self.template_variables = {}
        self.resolved_url = None
        self.available_qualities = []
        self.quality_info = []

    def prepare_recording(self, channel_uri, rec_dir, show_ads):
        """Prepare recording for template-based HLS stream."""
        super().prepare_recording(channel_uri, rec_dir, show_ads)
        logger.info("Preparing template-based HLS recording")
        self._extract_template_variables(channel_uri)

    def start(self, channel_uri, rec_dir, show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        """
        Start template HLS recording by resolving templates and delegating to appropriate recorder.

        Args:
            channel_uri (str): HLS playlist URL
            rec_dir (str): Output directory
            show_ads (bool): Whether to show ads
            buffering (int): Number of segments to buffer
            auth_tokens (dict | None): Authentication tokens (headers, cookies) for protected streams
            original_page_url (str | None): Original page URL for fallback/debugging
            all_sources (list | None): All available sources for potential fallbacks
        """
        logger.info("Starting template HLS recording for: %s", channel_uri)

        # Store authentication tokens and metadata
        self.auth_tokens = auth_tokens
        self.original_page_url = original_page_url
        self.all_sources = all_sources

        # First, prepare recording (this extracts template variables)
        self.prepare_recording(channel_uri, rec_dir, show_ads)

        # If this is a _TPL_ URL, resolve it to a concrete URL
        if "_TPL_" in channel_uri:
            resolved_uri = self._resolve_template_url(channel_uri)
            logger.info("Resolved template URL: %s -> %s", channel_uri, resolved_uri)
            self.resolved_url = resolved_uri

            # Detect if this is an M4S stream and delegate to appropriate recorder
            if self._is_m4s_stream(resolved_uri):
                logger.info("Detected M4S stream, delegating to M4S recorder")
                m4s_recorder = HLS_Recorder_M4S()
                m4s_recorder.socketserver = self.socketserver
                # Store original page URL for M4S recorder (needed for headers/cookies)
                if hasattr(self, 'original_page_url'):
                    m4s_recorder.original_page_url = self.original_page_url
                m4s_recorder.start(resolved_uri, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
            else:
                logger.info("Standard HLS stream, delegating to base recorder")
                super().start(resolved_uri, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
        else:
            # Check if this is an M4S stream even without _TPL_
            # (happens when master playlist resolution already occurred)
            if self._is_m4s_stream(channel_uri):  # pylint: disable=else-if-used
                logger.info("Detected M4S stream (already resolved), delegating to M4S recorder")
                m4s_recorder = HLS_Recorder_M4S()
                m4s_recorder.socketserver = self.socketserver
                # Pass metadata as parameters instead of attributes
                m4s_recorder.start(channel_uri, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
            else:
                # For other template types, use the base implementation
                logger.info("Non-M4S template detected, using base recorder")
                super().start(channel_uri, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)

    def _extract_template_variables(self, url):
        """Extract template variables from URL."""
        # Parse URL for common template variables
        parsed = urlparse(url)
        # Note: query_params could be used for future template variable extraction
        _ = parse_qs(parsed.query)  # Keep for future use

        # Look for _TPL_ placeholder (xHamster style)
        if "_TPL_" in url:
            logger.info("Found _TPL_ placeholder in URL, will parse qualities")
            self.template_variables['_TPL_'] = None

            # For _TPL_ URLs, we need to load the template playlist to get available qualities
            # The URL contains _TPL_ which needs to be replaced, but first we need to know what qualities are available
            logger.info("Loading template playlist to extract available qualities")
            self._load_and_parse_template_playlist(url)

            # Also try to extract from URL parameters as fallback
            if not self.available_qualities:
                logger.info("No qualities found in template playlist, trying URL parameters")
                self._parse_available_qualities(url)
            return

        # Look for common template patterns
        template_patterns = {
            'RepresentationID': r'\$RepresentationID\$',
            'Time': r'\$Time\$',
            'Number': r'\$Number\$',
            'Bandwidth': r'\$Bandwidth\$'
        }

        for var_name, pattern in template_patterns.items():
            if re.search(pattern, url):
                logger.info("Found template variable: %s", var_name)
                self.template_variables[var_name] = None

        logger.info("Template variables found: %s", list(self.template_variables.keys()))

    def _parse_available_qualities(self, url):
        """Parse available qualities from URL parameters (xHamster style)."""
        # Parse available qualities from the URL
        # Example: multi=256x144:144p:,426x240:240p:,854x480:480p:

        available_qualities = []
        multi_pattern = r'multi=([^/&]+)'
        multi_match = re.search(multi_pattern, url)

        if multi_match:
            multi_string = multi_match.group(1)
            # Extract quality info: resolution:quality:
            quality_pattern = r'(\d+x\d+):(\d+p):'
            quality_matches = re.findall(quality_pattern, multi_string)

            for _resolution, quality in quality_matches:
                available_qualities.append(quality)

            logger.info("Parsed available qualities from template URL: %s", available_qualities)
            self.available_qualities = available_qualities
        else:
            logger.warning("Could not parse qualities from template URL, will use defaults")
            self.available_qualities = ["720p", "480p", "360p", "240p"]

    def _resolve_template_url(self, template_url, segment_number=None, timestamp=None, preferred_quality="720p"):
        """
        Resolve template URL by substituting variables with actual values.
        """
        resolved_url = template_url

        # Handle _TPL_ placeholder (xHamster style)
        if "_TPL_" in resolved_url:
            selected_quality = self._select_best_quality(preferred_quality)
            resolved_url = resolved_url.replace("_TPL_", selected_quality)
            logger.info("Resolved _TPL_ placeholder: %s -> %s", template_url, resolved_url)
            return resolved_url

        # Replace common template variables
        if '$Number$' in resolved_url and segment_number is not None:
            resolved_url = resolved_url.replace('$Number$', str(segment_number))

        if '$Time$' in resolved_url and timestamp is not None:
            resolved_url = resolved_url.replace('$Time$', str(timestamp))

        # TODO: Add more template variable resolution logic
        # - $RepresentationID$ - representation identifier
        # - $Bandwidth$ - bitrate of the representation

        logger.debug("Resolved template URL: %s -> %s", template_url, resolved_url)
        return resolved_url

    def _select_best_quality(self, preferred_quality="720p"):
        """Select the best available quality from the parsed options."""
        if not self.available_qualities:
            logger.warning("No available qualities parsed, using fallback: %s", preferred_quality)
            return preferred_quality

        # Priority order: 1080p, 720p, 480p, 360p, 240p, 144p
        quality_preference = ["1080p", "720p", "480p", "360p", "240p", "144p"]

        selected_quality = None
        for pref_qual in quality_preference:
            if pref_qual in self.available_qualities:
                selected_quality = pref_qual
                break

        # If no preferred quality found, use the highest available
        if not selected_quality:
            # Sort by numeric value (highest first)
            numeric_qualities = []
            for q in self.available_qualities:
                try:
                    numeric_value = int(q.replace('p', ''))
                    numeric_qualities.append((numeric_value, q))
                except ValueError:
                    continue

            if numeric_qualities:
                numeric_qualities.sort(reverse=True)  # Highest first
                selected_quality = numeric_qualities[0][1]
            else:
                selected_quality = self.available_qualities[0]  # Fallback to first available

        logger.info("Selected quality: %s from available: %s", selected_quality, self.available_qualities)
        return selected_quality

    def _is_m4s_stream(self, url):
        """
        Detect if the resolved URL is an M4S (fragmented MP4) stream.
        """
        # Check for M4S indicators in the URL
        m4s_indicators = [
            '.av1.mp4.m3u8',    # xHamster AV1 format
            '.h264.mp4.m3u8',   # H.264 MP4 format
            '.h265.mp4.m3u8',   # H.265/HEVC MP4 format
            '.vp9.mp4.m3u8',    # VP9 MP4 format
            '.mp4.m3u8',        # General MP4 format
            '/mp4/',            # MP4 path indicator
            'format=mp4',       # MP4 format parameter
        ]

        url_lower = url.lower()
        for indicator in m4s_indicators:
            if indicator in url_lower:
                logger.info("M4S stream detected via indicator: %s", indicator)
                return True

        # Additional check: if we have quality info from template parsing
        if hasattr(self, 'quality_info') and self.quality_info:
            for quality in self.quality_info:
                if 'uri' in quality and '.mp4.m3u8' in quality['uri'].lower():
                    logger.info("M4S stream detected via quality info URI: %s", quality['uri'])
                    return True

        logger.info("Standard HLS stream detected (not M4S)")
        return False

    def _load_and_parse_template_playlist(self, template_url):
        """
        Load the template playlist and extract available qualities/variants.
        This parses the master playlist to get the available quality options.
        """
        try:
            session = get_session()
            response = session.get(template_url, timeout=10)
            response.raise_for_status()

            playlist_content = response.text
            logger.info("Loaded template playlist content (%d bytes)", len(playlist_content))
            logger.debug("Template playlist content:\n%s", playlist_content[:500])

            # Parse the master playlist using m3u8 library
            master_playlist = m3u8.loads(playlist_content)

            if master_playlist.playlists:
                # Extract qualities from the master playlist variants
                qualities = []
                quality_info = []

                for variant in master_playlist.playlists:
                    if variant.stream_info and variant.stream_info.resolution:
                        # Extract quality from resolution (e.g., 1280x720 -> 720p)
                        width, height = variant.stream_info.resolution
                        quality = f"{height}p"

                        # Also get the variant URI (like "720p.av1.mp4.m3u8")
                        variant_uri = variant.uri

                        qualities.append(quality)
                        quality_info.append({
                            'quality': quality,
                            'resolution': f"{width}x{height}",
                            'bandwidth': variant.stream_info.bandwidth,
                            'uri': variant_uri
                        })

                        logger.info("Found variant: %s (%sx%s, %d bps) -> %s",
                                    quality, width, height, variant.stream_info.bandwidth or 0, variant_uri)

                if qualities:
                    logger.info("Extracted qualities from master playlist: %s", qualities)
                    self.available_qualities = qualities
                    self.quality_info = quality_info  # Store detailed info for future use
                    return

            # No valid master playlist found - use defaults
            logger.warning("No master playlist variants found, using default qualities")
            self.available_qualities = ["720p", "480p", "360p", "240p"]

        except Exception as e:
            logger.error("Failed to load template playlist: %s", e)
            # Fallback to default qualities
            self.available_qualities = ["720p", "480p", "360p", "240p"]

    def process_playlist_content(self, playlist_text):
        """
        Process playlist content with template resolution.
        """
        # First, check if we need to resolve any templates in the playlist
        if re.search(r'\$\w+\$', playlist_text) or '_TPL_' in playlist_text:
            logger.info("Playlist contains templates, resolving...")

            # Resolve _TPL_ placeholders in playlist content
            if '_TPL_' in playlist_text:
                selected_quality = self._select_best_quality()
                playlist_text = playlist_text.replace('_TPL_', selected_quality)
                logger.info("Resolved _TPL_ placeholders in playlist content with quality: %s", selected_quality)

            # Resolve other template variables if needed
            if re.search(r'\$\w+\$', playlist_text):
                # For now, handle basic template patterns
                # In a real implementation, you'd need to determine appropriate values
                # based on the stream context, timing, and segment numbering
                logger.warning("Standard template variables found but not yet fully implemented")

        playlist = super().process_playlist_content(playlist_text)

        logger.debug("Processing template-based playlist with %d segments",
                     len(playlist.segments) if playlist.segments else 0)

        return playlist

    def calculate_sleep_duration(self, target_duration):
        """
        For template-based streams, timing depends on the template type.
        """
        # Template streams might need different timing based on their nature
        return min(target_duration / 3, 2.0) if target_duration else 1.0

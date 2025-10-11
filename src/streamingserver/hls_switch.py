# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Stream Type Switcher
"""

from __future__ import annotations

import re
from urllib.request import urlopen
from urllib.parse import urljoin
import m3u8
from hls_recorder_live import HLS_Recorder_Live
from hls_recorder_base import HLS_Recorder_Base
from hls_recorder_template import HLS_Recorder_Template
from session_utils import get_session
from debug import get_logger

logger = get_logger(__file__)


class HLSType:
    TEMPLATE = "template"        # Template-based URLs or M4S streams
    MASTER = "master"            # Master playlist with variants
    LIVE = "live"                # Live stream without #EXT-X-ENDLIST
    VOD = "vod"                  # VOD with #EXT-X-ENDLIST
    UNKNOWN = "unknown"


class HLS_Switch:

    def __init__(self, socketserver=None):
        self.active_recorder = None
        self.socketserver = socketserver
        self.auth_tokens = None
        self.all_sources = None
        self.original_page_url = None

    def load_master_playlist(self, url):
        """Load playlist text from URL with authentication if available"""
        try:
            logger.info("DEBUG: Loading playlist from URL: %s", url)
            if url.startswith("http"):
                # Use session utils for better compatibility
                try:
                    session = get_session()

                    # Apply authentication tokens if available
                    if self.auth_tokens:
                        # Check if auth_tokens is dict-like (legacy format)
                        if isinstance(self.auth_tokens, dict):
                            # Apply cookies
                            try:
                                if hasattr(self.auth_tokens, 'get') and self.auth_tokens.get("cookies"):
                                    cookies = self.auth_tokens.get("cookies")
                                    if cookies:
                                        session.cookies.update(cookies)
                                        logger.info("DEBUG: Applied %d cookies for playlist loading", len(cookies))
                            except (TypeError, AttributeError):
                                pass

                            # Apply headers
                            try:
                                if hasattr(self.auth_tokens, 'get') and self.auth_tokens.get("headers"):
                                    headers = self.auth_tokens.get("headers")
                                    if headers:
                                        session.headers.update(headers)
                                        logger.info("DEBUG: Applied %d headers for playlist loading", len(headers))
                            except (TypeError, AttributeError):
                                pass
                        # Check if auth_tokens is AuthTokens class instance
                        elif hasattr(self.auth_tokens, 'headers') and hasattr(self.auth_tokens, 'cookies'):
                            # Apply cookies from AuthTokens instance
                            if self.auth_tokens.cookies:
                                session.cookies.update(self.auth_tokens.cookies)
                                logger.info("DEBUG: Applied %d cookies from AuthTokens for playlist loading", len(self.auth_tokens.cookies))

                            # Apply headers from AuthTokens instance
                            if self.auth_tokens.headers:
                                session.headers.update(self.auth_tokens.headers)
                                logger.info("DEBUG: Applied %d headers from AuthTokens for playlist loading", len(self.auth_tokens.headers))

                    response = session.get(url, timeout=10)
                    response.raise_for_status()
                    content = response.text
                    final_url = str(response.url)
                    logger.info("DEBUG: Successfully loaded %d bytes via session from URL", len(content))
                    logger.info("DEBUG: Response headers: %s", dict(response.headers))
                    logger.info("DEBUG: Final URL after redirects: %s", final_url)
                    return content, final_url
                except ImportError:
                    # Fallback to urllib if session_utils not available
                    with urlopen(url) as resp:
                        content = resp.read().decode("utf-8", errors="ignore")
                        logger.info("DEBUG: Successfully loaded %d bytes via urllib from URL", len(content))
                        return content, url  # No redirect info available with urllib
            else:
                with open(url, "r", encoding="utf-8") as f:
                    content = f.read()
                    logger.info("DEBUG: Successfully loaded %d bytes from file", len(content))
                    return content, url
        except Exception as e:
            logger.error("Cannot load playlist: %s (%s)", url, e)
            return None, None

    def analyze_type(self, content):
        """Detect playlist type from its tags/content."""
        if not content:
            return HLSType.UNKNOWN

        # Check if it's a master playlist first
        if "#EXT-X-STREAM-INF" in content:
            return HLSType.MASTER

        # Check for template-based URLs (more specific patterns)
        if re.search(r"\$(RepresentationID|Time|Number|Bandwidth)\$", content):
            return HLSType.TEMPLATE

        # Check for xHamster-style _TPL_ placeholders
        if "_TPL_" in content:
            return HLSType.TEMPLATE

        # Check for M4S-based HLS (fragmented MP4) - needs special M4S recorder
        if ".m4s" in content.lower() or "#EXT-X-MAP" in content:
            logger.info("Detected M4S content indicators in playlist")
            # M4S streams need the M4S recorder, not standard
            # Route to TEMPLATE type which will detect M4S and delegate appropriately
            return HLSType.TEMPLATE

        # Distinguish between Live and VOD
        if "#EXT-X-MEDIA-SEQUENCE" in content:
            if "#EXT-X-ENDLIST" in content:
                return HLSType.VOD  # Complete playlist
            return HLSType.LIVE  # Live stream (no endlist)

        return HLSType.UNKNOWN

    def get_media_playlist_url(self, master_content, master_url, final_redirect_url=None):
        """Extract the best quality media playlist URL from master playlist."""
        try:
            logger.info("DEBUG: Master playlist content (first 500 chars): %s", master_content[:500])
            master_playlist = m3u8.loads(master_content)
            logger.info("DEBUG: Parsed master playlist, found %d playlists", len(master_playlist.playlists) if master_playlist.playlists else 0)

            if master_playlist.playlists:
                # Log all available playlists
                for i, playlist in enumerate(master_playlist.playlists):
                    logger.info("DEBUG: Playlist %d: URI=%s, Bandwidth=%s", i, playlist.uri, playlist.stream_info.bandwidth)

                # Get the highest bandwidth stream, limited to 2.5 Mbps
                MAX_BANDWIDTH = 2500000

                # Filter playlists within bandwidth limit
                eligible_playlists = [p for p in master_playlist.playlists
                                      if (p.stream_info.bandwidth or 0) <= MAX_BANDWIDTH]

                # If no playlists within limit, use the lowest bandwidth one
                if not eligible_playlists:
                    logger.info("DEBUG: No playlists within %d bandwidth limit, selecting lowest", MAX_BANDWIDTH)
                    best_playlist = min(master_playlist.playlists,
                                        key=lambda p: p.stream_info.bandwidth or 0)
                else:
                    # Get the highest bandwidth within the limit
                    best_playlist = max(eligible_playlists,
                                        key=lambda p: p.stream_info.bandwidth or 0)
                    logger.info("DEBUG: Selected playlist within %d bandwidth limit", MAX_BANDWIDTH)

                logger.info("DEBUG: Selected best playlist: URI=%s, Bandwidth=%s", best_playlist.uri, best_playlist.stream_info.bandwidth)

                # Use final redirect URL as base if available, otherwise use master_url
                base_url = final_redirect_url if final_redirect_url else master_url
                logger.info("DEBUG: Using base URL for joining: %s", base_url)

                # Handle relative URLs
                if best_playlist.uri.startswith('http'):
                    final_url = best_playlist.uri
                else:
                    final_url = urljoin(base_url, best_playlist.uri)

                logger.info("DEBUG: Final media playlist URL: %s", final_url)
                return final_url

            # If no playlists found, assume it's already a media playlist
            logger.info("DEBUG: No playlists found, using master URL as media URL")
            return master_url

        except Exception as e:
            logger.warning("Failed to parse master playlist: %s", e)
            logger.info("DEBUG: Raw master content: %s", repr(master_content[:200]))
            return master_url

    # ────────────────────────────────────────────────────────────────
    # THE SWITCH — main control logic
    # ────────────────────────────────────────────────────────────────
    def hls_switch(self, master_url, rec_dir, show_ads, buffering, auth_tokens=None, original_page_url=None, all_sources=None):
        # Store auth tokens and metadata for use throughout the switch process
        self.auth_tokens = auth_tokens
        self.original_page_url = original_page_url
        self.all_sources = all_sources

        content, final_master_url = self.load_master_playlist(master_url)

        if not content:
            logger.error("Failed to load master playlist.")
            return

        playlist_type = self.analyze_type(content)
        logger.info("Detected HLS type: %s", playlist_type)

        # Initialize recording URL
        recording_url = master_url

        # Handle master playlist by getting media playlist
        if playlist_type == HLSType.MASTER:
            logger.info("Master playlist detected, extracting media playlist URL")
            media_url = self.get_media_playlist_url(content, master_url, final_master_url)

            # Analyze the media playlist
            media_content, _ = self.load_master_playlist(media_url)
            if media_content:
                playlist_type = self.analyze_type(media_content)
                logger.info("Media playlist type: %s", playlist_type)
                recording_url = media_url
            else:
                logger.error("Failed to load media playlist from: %s", media_url)
                # Try to fallback - maybe the master URL is actually the media URL
                logger.info("DEBUG: Trying to use master URL directly as fallback")
                playlist_type = self.analyze_type(content)
                if playlist_type not in (HLSType.MASTER, HLSType.UNKNOWN):
                    logger.info("DEBUG: Master playlist can be used as media playlist, type: %s", playlist_type)
                    recording_url = master_url
                else:
                    return

        logger.info("DEBUG: Using URL for recording: %s", recording_url)

        # Route to appropriate recorder
        if playlist_type == HLSType.TEMPLATE:
            logger.info("Detected template/M4S HLS stream.")
            self.active_recorder = HLS_Recorder_Template()
            self.active_recorder.socketserver = self.socketserver
            self.active_recorder.start(recording_url, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
        elif playlist_type == HLSType.LIVE:
            logger.info("Detected live HLS stream.")
            self.active_recorder = HLS_Recorder_Live()
            self.active_recorder.socketserver = self.socketserver
            logger.info("DEBUG: socketserver assigned to recorder: %s", self.socketserver)
            self.active_recorder.start(recording_url, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
        elif playlist_type == HLSType.VOD:
            logger.info("Detected VOD HLS stream.")
            self.active_recorder = HLS_Recorder_Base()
            self.active_recorder.socketserver = self.socketserver
            logger.info("DEBUG: socketserver assigned to recorder: %s", self.socketserver)
            self.active_recorder.start(recording_url, rec_dir, show_ads, buffering, auth_tokens, original_page_url, all_sources)
        elif playlist_type in (HLSType.MASTER, HLSType.UNKNOWN):
            logger.warning("Unsupported HLS type: %s", playlist_type)
        else:
            logger.warning("Unknown HLS type: %s", playlist_type)

    def stop(self):
        """Stop the active HLS recorder."""
        if self.active_recorder:
            logger.info("Stopping HLS recorder via switch")
            self.active_recorder.stop()
            self.active_recorder = None

# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
HLS Playlist Utilities

This module provides helper functions for fetching and parsing HLS (HTTP Live
Streaming) playlists. It includes functionality to retrieve a master playlist,
select the best quality stream, fetch media playlists, and parse HLS attribute
strings. It also contains a utility to compare URIs to detect changes in the
stream's source.
"""

import os
from urllib.parse import urljoin
from urllib.parse import urlparse
import m3u8
from drm_utils import detect_drm_in_content
from debug import get_logger

logger = get_logger(__file__)


def get_master_playlist(session, url):
    """
    Fetches a master HLS playlist and returns the URL of the highest quality stream.

    If the provided URL points to a media playlist directly, it returns the same URL.
    Otherwise, it parses the master playlist, sorts the available streams by
    bandwidth, and selects the one with the highest bandwidth.

    Args:
        session (requests.Session): A requests.Session object used for making HTTP requests.
        url (str): The URL of the master HLS playlist.

    Returns:
        str | None: The URL of the highest quality media playlist, or None if
                    an error occurs.
    """
    try:
        logger.debug("Getting master playlist from: %s", url)
        response = session.get(url, allow_redirects=True, timeout=15)
        response.raise_for_status()

        # Parse the master playlist
        master_playlist = m3u8.loads(response.text)
        logger.debug("Master playlist fetched: %s streams found", len(master_playlist.playlists))

        # Check for DRM protection in master playlist
        if detect_drm_in_content(response.text, content_type="m3u8")["has_drm"]:
            logger.error("DRM protection detected in master playlist")
            raise ValueError("DRM_PROTECTED: DRM protection detected in HLS master playlist")

        playlist_root = response.url  # Use the final URL after redirects
        logger.debug("📍 Master playlist root URL: %s", playlist_root)

        if master_playlist.playlists:
            # Sort by bandwidth and get the best quality
            sorted_playlists = sorted(
                master_playlist.playlists,
                key=lambda p: p.stream_info.bandwidth if p.stream_info and p.stream_info.bandwidth is not None else 0
            )

            # Get highest quality
            best_playlist = sorted_playlists[-1]
            logger.debug("Best quality uri: %s", best_playlist.uri)
            media_url = urljoin(playlist_root, best_playlist.uri)
            logger.debug("Media playlist URL: %s", media_url)

            bandwidth = best_playlist.stream_info.bandwidth if best_playlist.stream_info else 0
            resolution = best_playlist.stream_info.resolution if best_playlist.stream_info and best_playlist.stream_info.resolution else "unknown"

            logger.debug("Selected stream: %skbps, %s resolution", bandwidth // 1000, resolution)
            logger.debug("Media playlist URL: %s", media_url)

            return media_url
        # Already a media playlist
        logger.debug("Direct media playlist URL: %s", url)
        return url

    except Exception as e:
        logger.error("Error getting master playlist: %s", e)
        return None


def get_playlist(session, playlist_url):
    """
    Fetches the content of an HLS media playlist.

    Args:
        session (requests.Session): A requests.Session object for HTTP requests.
        playlist_url (str): The URL of the media playlist to fetch.

    Returns:
        str | None: The text content of the playlist, or None if the request fails.
    """
    try:
        response = session.get(playlist_url, timeout=5)
        logger.debug("Fetched playlist (%s bytes)", len(response.text))
    except Exception as e:
        logger.error("Error fetching playlist: %s", e)
        return None
    return response.text


def different_uris(uri1, uri2):
    """
    Compares two URIs to determine if they point to different stream sources based on directory path.

    URIs are considered different if the directory path (the part between the host and the filename)
    is different, regardless of the host. This helps in detecting when a stream switches to a different
    content path, even if served from different CDNs or hosts.

    Args:
        uri1 (str): The first URI to compare.
        uri2 (str): The second URI to compare.

    Returns:
        bool: True if the directory paths are different, False otherwise.
    """
    if not uri1 or not uri2:
        return True  # If either is None, consider them different

    # Parse both URIs
    parsed1 = urlparse(uri1)
    parsed2 = urlparse(uri2)

    # Extract host (netloc)
    # host1 = parsed1.netloc
    # host2 = parsed2.netloc

    # Extract directory path (excluding filename)
    dir1 = os.path.dirname(parsed1.path)
    dir2 = os.path.dirname(parsed2.path)

    # Different if directory paths are different
    return dir1 != dir2

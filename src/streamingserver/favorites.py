# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Favorites Management for PlutoTV Channels

This module provides functionality to filter PlutoTV channels based on a user-defined
list of favorite channel "slugs". It can read a favorites file, filter a list of
channels, and generate a new M3U playlist containing only the favorite channels.
"""
from pathlib import Path
from debug import get_logger

logger = get_logger(__file__)


def lookup_slugs(favorite_slugs_file_path):
    """
    Reads a list of channel slugs from a text file.

    The file should contain one slug per line. Lines that are empty or start
    with '#' are ignored.

    Args:
        favorite_slugs_file_path (str): The path to the file containing favorite slugs.

    Returns:
        list[str]: A list of favorite channel slugs. Returns an empty list if
                   the file does not exist.
    """
    path = Path(favorite_slugs_file_path)
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class FavoritesFilter:
    """
    A callable filter for PlutoTV channels based on a list of favorite slugs.

    This class can be used as a filter function to determine if a channel
    should be included in a list of favorites. It also tracks which favorite
    slugs were used and which were not found.

    Attributes:
        _favorite_slugs (list[str]): The list of slugs to filter by.
        _tracker (dict[str, int]): A dictionary to track the usage count of each slug.
        _channel_count (int): The total number of channels processed by the filter.
    """
    def __init__(self, favorite_slugs):
        """
        Initializes the FavoritesFilter.

        Args:
            favorite_slugs (list[str]): A list of favorite channel slugs.
        """
        self._favorite_slugs = favorite_slugs
        self._tracker = {slug: 0 for slug in favorite_slugs}
        self._channel_count = 0

    def __call__(self, channel):
        """
        Determines if a channel is a favorite.

        This method is called for each channel to be filtered. It increments
        the usage tracker if the channel's slug is in the favorites list.

        Args:
            channel (dict): A dictionary representing a single channel.

        Returns:
            bool: True if the channel is a favorite, False otherwise.
        """
        self._channel_count += 1
        slug = channel.get("slug")
        if slug in self._tracker:
            self._tracker[slug] += 1
            return True
        return False

    def is_empty(self):
        """Checks if the favorites list is empty."""
        return len(self._favorite_slugs) == 0

    def unused_favorite_slugs(self):
        """Returns a list of favorite slugs that were not found in any channel."""
        return [slug for slug, count in self._tracker.items() if count == 0]

    def print_summary(self):
        """Prints a summary of the filtering results to the debug log."""
        unused = self.unused_favorite_slugs()
        used = len(self._favorite_slugs) - len(unused)
        logger.debug("Filter Returned %s/%s channels", used, self._channel_count)
        if unused:
            logger.debug("Unknown Favorite Slugs: %s", unused)


def from_favorites(favorite_slugs_file_path):
    """
    Creates a FavoritesFilter instance from a favorites file.

    This is a convenience factory function that reads the slugs from a file
    and initializes a `FavoritesFilter` with them.

    Args:
        favorite_slugs_file_path (str): The path to the favorites file.

    Returns:
        FavoritesFilter: An instance of the filter.
    """
    favorite_slugs = lookup_slugs(favorite_slugs_file_path)
    return FavoritesFilter(favorite_slugs)


def generate_favorites_m3u(channels, favorite_slugs_path, output_path):
    """
    Generates an M3U playlist file containing only favorite channels.

    This function filters a list of channels using a favorites file and writes
    the resulting favorite channels to a new M3U file, preserving their
    metadata and using their most current stream URLs.

    Args:
        channels (list[dict]): The full list of channel dictionaries to filter.
        favorite_slugs_path (str): The path to the file containing favorite slugs.
        output_path (str): The path where the generated M3U file will be saved.

    Returns:
        bool: True if the favorites M3U was generated, False if the favorites
              list was empty and no file was created.
    """
    favorites_filter = from_favorites(favorite_slugs_path)
    if favorites_filter.is_empty():
        return False

    fav_channels = [c for c in channels if favorites_filter(c)]

    # Generate M3U content using the CURRENT channel data
    m3u8 = "#EXTM3U\n"
    for channel in fav_channels:
        # Use all the latest channel data
        slug = channel["slug"]
        logo = channel.get("colorLogoPNG", {}).get("path", "")
        group = channel.get("category", "")
        name = channel["name"]

        # Get current, fresh URL with updated parameters
        url = channel["stitched"]["urls"][0]["url"]

        m3u8 += f'#EXTINF:0 tvg-id="{slug}" tvg-logo="{logo}" group-title="{group}", {name}\n{url}\n\n'

    Path(output_path).write_text(m3u8, encoding="utf-8")
    logger.debug(
        "Generated favorites M3U with %s channels to %s",
        len(fav_channels),
        output_path,
    )
    return True

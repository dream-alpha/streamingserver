from pathlib import Path
from debug import get_logger

logger = get_logger(__name__, "DEBUG")


def lookup_slugs(favorite_slugs_file_path):
    path = Path(favorite_slugs_file_path)
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class FavoritesFilter:
    def __init__(self, favorite_slugs):
        self._favorite_slugs = favorite_slugs
        self._tracker = {slug: 0 for slug in favorite_slugs}
        self._channel_count = 0

    def __call__(self, channel):
        self._channel_count += 1
        slug = channel.get("slug")
        if slug in self._tracker:
            self._tracker[slug] += 1
            return True
        return False

    def is_empty(self):
        return len(self._favorite_slugs) == 0

    def unused_favorite_slugs(self):
        return [slug for slug, count in self._tracker.items() if count == 0]

    def print_summary(self):
        unused = self.unused_favorite_slugs()
        used = len(self._favorite_slugs) - len(unused)
        logger.debug(f"[INFO] Filter Returned {used}/{self._channel_count} channels")
        if unused:
            logger.debug(f"Unknown Favorite Slugs: {unused}")


def from_favorites(favorite_slugs_file_path):
    favorite_slugs = lookup_slugs(favorite_slugs_file_path)
    return FavoritesFilter(favorite_slugs)


def generate_favorites_m3u(channels, favorite_slugs_path, output_path):
    """Generate an M3U file with only favorite channels using current data"""
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
        f"Generated favorites M3U with {len(fav_channels)} channels to {output_path}"
    )
    return True

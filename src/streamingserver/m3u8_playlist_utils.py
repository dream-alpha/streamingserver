"""
M3U8 Playlist Parsing Utilities

This module provides functions for reading and parsing M3U8 playlist files.
It is designed to extract structured information from standard M3U8 `#EXTINF`
entries, including duration, display name, attributes (like `tvg-id`, `tvg-logo`),
and the associated stream URL.
"""
import re


def get_playlist(file_path: str) -> list[dict]:
    """
    Reads an M3U8 playlist file and parses its entries.

    Args:
        file_path (str): The path to the M3U8 file.

    Returns:
        list[dict]: A list of dictionaries, where each dictionary represents
                    a channel entry from the playlist.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        m3u8_text = f.read()
    return parse_m3u8_entry(m3u8_text)


def parse_m3u8_entry(m3u8_text: str) -> list[dict]:
    """
    Parses #EXTINF entries from an M3U8 playlist string.

    This function extracts information from `#EXTINF` lines, including duration,
    key-value attributes (e.g., `tvg-id`), the display name, and the URL on the
    following line. The resulting list of entries is sorted by display name.

    Args:
        m3u8_text (str): The full text content of the M3U8 playlist.

    Returns:
        list[dict]: A sorted list of dictionaries, where each dictionary
                    represents a channel and contains keys like 'duration',
                    'display_name', 'channel_uri', 'tvg-id', etc.
    """
    lines = m3u8_text.strip().splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            # Parse duration and attributes
            extinf = line[8:]
            # Split duration and rest
            if ' ' in extinf:
                duration, rest = extinf.split(' ', 1)
            else:
                duration, rest = extinf, ''
            duration = float(duration)
            # Parse attributes (key="value")
            attr_pattern = r'(\w+?)="([^"]*?)"'
            attrs = dict(re.findall(attr_pattern, rest))
            # Parse display_name (after last comma)
            display_name = rest.split(',', 1)[-1].strip() if ',' in rest else ''
            # Next line is the URL
            uri = ''
            if i + 1 < len(lines):
                uri = lines[i + 1].strip()
            if display_name.startswith('Pluto TV'):
                display_name = display_name.replace('Pluto TV', '', 1).strip()
            entry = {'duration': duration, 'display_name': display_name, 'channel_uri': uri}
            # Add known attribute keys explicitly
            for key in ("tvg-id", "tvg-logo", "group-title"):
                if key in attrs:
                    entry[key] = attrs[key]
            result.append(entry)
            i += 2
        else:
            i += 1

        # Sort channels by display_name (case-insensitive, None last)
        result.sort(key=lambda c: (c['display_name'] is None, (c['display_name'] or '').lower()))
    return result

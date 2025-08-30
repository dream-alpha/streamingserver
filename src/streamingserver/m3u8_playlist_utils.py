"""
M3U8 Playlist Parsing Utilities

This module provides functions for reading and parsing M3U8 playlist files.
It is designed to extract structured information from standard M3U8 `#EXTINF`
entries, including duration, display name, attributes (like `tvg-id`, `tvg-logo`),
and the associated stream URL.
"""


import re
from collections import defaultdict


def get_playlist_groups(filepath):
    groups = defaultdict(list)
    current_info = None

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                # Extract attributes and channel name
                match = re.search(r'group-title="([^"]+)"', line)
                group = match.group(1) if match else "Unknown"
                # Extract channel name (after last comma)
                channel_name = line.split(",")[-1].strip()
                # Extract logo and tvg-id if needed
                logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                logo = logo_match.group(1) if logo_match else ""
                tvg_id_match = re.search(r'tvg-id="([^"]+)"', line)
                tvg_id = tvg_id_match.group(1) if tvg_id_match else ""
                current_info = {
                    "name": channel_name,
                    "logo": logo,
                    "tvg_id": tvg_id,
                    "url": None
                }
                current_group = group
            elif line and not line.startswith("#"):
                if current_info:
                    current_info["url"] = line  # pylint: disable=unsupported-assignment-operation
                    groups[current_group].append(current_info)
                    current_info = None
    return dict(groups)


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
    lines = m3u8_text.strip().splitlines()
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            extinf = line[8:]
            if ' ' in extinf:
                duration, rest = extinf.split(' ', 1)
            else:
                duration, rest = extinf, ''
            duration = float(duration)
            attr_pattern = r'(\w+?)="([^"]*?)"'
            attrs = dict(re.findall(attr_pattern, rest))
            name = rest.split(',', 1)[-1].strip() if ',' in rest else ''
            url = ''
            if i + 1 < len(lines):
                url = lines[i + 1].strip()
            if name.startswith('Pluto TV'):
                name = name.replace('Pluto TV', '', 1).strip()
            entry = {'duration': duration, 'name': name, 'url': url}
            for key in ("tvg-id", "tvg-logo", "group-title"):
                if key in attrs:
                    entry[key] = attrs[key]
            result.append(entry)
            i += 2
        else:
            i += 1
    result.sort(key=lambda c: (c['name'] is None, (c['name'] or '').lower()))
    return result

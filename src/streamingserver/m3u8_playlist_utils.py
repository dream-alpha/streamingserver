import re


def get_playlist(file_path: str) -> list[dict]:
    """
    Read an m3u8 playlist file and return a list of entries with duration, display name, channel, and URL.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        m3u8_text = f.read()
    return parse_m3u8_entry(m3u8_text)


def parse_m3u8_entry(m3u8_text: str) -> list[dict]:
    """
    Parse EXTINF entries from an m3u8 playlist with attributes and URL.
    Returns a list of dicts with duration, attributes, name, and url.
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
            url = ''
            channel_id = ''
            if i + 1 < len(lines):
                url = lines[i + 1].strip()
                # Extract channel slug from URL after '/channel/'
                m = re.search(r'/channel/([^/]+)', url)
                if m:
                    channel_id = m.group(1)
            if display_name.startswith('Pluto TV'):
                display_name = display_name.replace('Pluto TV', '', 1).strip()
            entry = {'duration': duration, 'display_name': display_name, 'channel_id': channel_id}
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

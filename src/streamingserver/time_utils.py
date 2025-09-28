# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import datetime


def parse_iso8601(dtstr):
    """
    Parse ISO 8601 datetime string using Python 3's built-in fromisoformat.
    Returns datetime object or None if parsing fails.
    """
    try:
        # Handle timezone info by removing 'Z' suffix if present
        if dtstr.endswith('Z'):
            dtstr = dtstr[:-1] + '+00:00'
        return datetime.datetime.fromisoformat(dtstr)
    except (ValueError, AttributeError):
        return None

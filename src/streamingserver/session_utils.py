# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import requests
from auth_utils import get_headers


def get_session():
    """
    Get a session for HTTP requests.

    Returns:
        requests.Session: Session object with fully loaded browser-like headers set.
    """

    # Create a new session with full browser headers for better compatibility with streaming sites
    session = requests.Session()
    session.headers.update(get_headers("browser"))
    return session

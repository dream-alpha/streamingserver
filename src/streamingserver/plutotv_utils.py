# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
PlutoTV channel and epg utils

This module provides functions to fetch channel and program data from the
PlutoTV API and creates a dict that allows easy and fast access to channel
and epg data.
"""

import os
import re
import uuid
import json
import time
import threading
import datetime
import urllib.parse
from pathlib import Path
import requests
from debug import get_logger

logger = get_logger(__file__)

ENIGMA2_ETC_DIR = Path("/etc/enigma2") / "UVP" / "PlutoTV"
CHANNELS_CACHE_FILE = ENIGMA2_ETC_DIR / "channels.json"
CATEGORIES_CACHE_FILE = ENIGMA2_ETC_DIR / "categories.json"

DATA_DIR = Path("/root/plugins/streamingserver/data")
CACHE_FILE = DATA_DIR / "cache.json"

if not ENIGMA2_ETC_DIR.exists():
    os.makedirs(ENIGMA2_ETC_DIR)


def make_valid_filename(s, replacement="_"):
    """
    Converts a string to a valid filename by replacing invalid characters.
    """
    # Remove or replace invalid filename characters: \ / : * ? " < > | and control chars
    s = re.sub(r'[\\/:*?"<>|\r\n\t ]', replacement, s)
    # Optionally, strip leading/trailing whitespace and dots
    s = s.strip().strip('.')
    return s


def update_channel_data():
    """
    Runs create_channel_tree every 30 minutes in a background thread. Call this function to start the loop.
    """
    def update_loop():
        while True:
            time.sleep(1800)  # 30 minutes
            try:
                create_channel_data()
            except Exception as e:
                logger.error(f"Error in create_channel_data: {e}")
    create_channel_data()  # Initial call
    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()


def create_channel_data():
    channels = fetch_json()
    if not channels:
        return None, None
    categories_dict = {}
    category_names_list = []
    categories_list = []
    for channel in channels:
        channel["url"] = build_url(channel)
        category = channel.get('category', 'Other')
        if category not in category_names_list:
            category_names_list.append(category)
        categories_dict.setdefault(category, []).append(channel)

    for category in categories_dict:  # pylint: disable=consider-using-dict-items
        categories_dict[category].sort(key=lambda x: x['name'])

    with CHANNELS_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(categories_dict, f)

    category_names_list.sort()
    for category in category_names_list:
        categories_list.append(
            {
                "name": category,
                "icon": make_valid_filename(category) + ".png"
            }
        )
    with CATEGORIES_CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(categories_list, f)
    return categories_list, categories_dict


def fetch_json():
    """
    Fetches channel and EPG data from the PlutoTV API.

    This function retrieves the next 48 hours of programming information.
    It uses a local cache (`cache.json`) to avoid making repeated API calls
    if the cached data is less than 30 minutes old.

    Returns:
        dict: A dictionary containing the JSON response from the PlutoTV API.
    """
    logger.debug("Fetching channel data...")

    if CACHE_FILE.exists():
        age = (
            datetime.datetime.now()
            - datetime.datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        ).total_seconds()
        if age <= 1800:
            logger.info("No update required.")
            return None

    start = urllib.parse.quote(
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:00:00.000+0000")
    )
    stop = urllib.parse.quote(
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=48)).strftime(
            "%Y-%m-%d %H:00:00.000+0000"
        )
    )
    url = f"http://api.pluto.tv/v2/channels?start={start}&stop={stop}"
    logger.debug("url: %s", url)
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    # Store as pretty-printed JSON
    try:
        parsed_json = response.json()
        CACHE_FILE.write_text(
            json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Failed to pretty-print JSON: {e}")
        CACHE_FILE.write_text(response.text, encoding="utf-8")
    logger.debug("Using api.pluto.tv, writing cache.json.")
    return response.json()


def build_url(channel):
    if not channel.get("isStitched"):
        logger.debug("Skipping 'fake' channel %s.", channel['name'])
        return "invalid"

    stitched_url = channel["stitched"]["urls"][0]["url"]
    parsed_url = urllib.parse.urlparse(stitched_url)
    params = urllib.parse.parse_qs(parsed_url.query)

    # Update existing parameters or add new ones
    device_id = str(uuid.uuid1())
    sid = str(uuid.uuid4())
    params.update(
        {
            "advertisingId": [""],
            "appName": ["web"],
            "appVersion": ["unknown"],
            "appStoreUrl": [""],
            "architecture": [""],
            "buildVersion": [""],
            "clientTime": ["0"],
            "deviceDNT": ["0"],
            "deviceId": [device_id],
            "deviceMake": ["Chrome"],
            "deviceModel": ["web"],
            "deviceType": ["web"],
            "deviceVersion": ["unknown"],
            "includeExtendedEvents": ["false"],
            "sid": [sid],
            "userId": [""],
            "serverSideAds": ["true"],
        }
    )

    updated_url = urllib.parse.urlunparse(
        parsed_url._replace(query=urllib.parse.urlencode(params, doseq=True))
    )

    return updated_url


def main():
    create_channel_data()


if __name__ == "__main__":
    main()

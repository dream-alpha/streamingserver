# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
PlutoTV channel and epg utils

This module provides functions to fetch channel and program data from the
PlutoTV API and creates a dict that allows easy and fast access to channel
and epg data.
"""

import os
import uuid
import json
import time
import threading
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
import requests
from favorites import from_favorites
from debug import get_logger

logger = get_logger(__file__)

DATA_DIR = Path("/root/plugins/streamingserver/data")
CHANNEL_EPG_CACHE = Path("/etc/enigma2/plutotv-channel-epg-cache.json")
CACHE_FILE = Path("/etc/enigma2/cache.json")
FAVORITES_PATH = DATA_DIR / "plutotv-favorites"
EPG_FILE = DATA_DIR / "plutotv-epg.xml"
PLAYLIST_FILE = DATA_DIR / "plutotv-playlist.m3u8"


def update_channel_epg_cache():
    """
    Runs create_channel_epg_cache every 30 minutes in a background thread. Call this function to start the loop.
    """
    def epg_loop():
        while True:
            time.sleep(1800)  # 30 minutes
            try:
                create_channel_epg_cache()
            except Exception as e:
                logger.error(f"Error in create_channel_epg_cache: {e}")
    create_channel_epg_cache()  # Initial call
    thread = threading.Thread(target=epg_loop, daemon=True)
    thread.start()


def create_channel_epg_cache():
    """
    Fetches channel and EPG data from the PlutoTV API.

    This function retrieves the next 48 hours of programming information.
    It uses a local cache (`cache.json`) to avoid making repeated API calls
    if the cached data is less than 30 minutes old.

    Returns:
        dict: A dictionary containing the JSON response from the PlutoTV API.
    """

    logger.info("Grabbing EPG...")

    if os.path.exists(CHANNEL_EPG_CACHE):
        age = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.datetime.fromtimestamp(os.path.getmtime(CHANNEL_EPG_CACHE), datetime.timezone.utc)
        ).total_seconds()
        if age <= 1800:
            logger.info("Using pluto-channel-epg-cache.json, it's under 30 minutes old.")
            with open(CHANNEL_EPG_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            logger.info("Cache is too old, fetching new data.")
    else:
        logger.info("Cache file does not exist, fetching new data.")

    # Fetch and write new cache
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start = urllib.parse.quote(
        now_utc.strftime("%Y-%m-%d %H:00:00.000+0000")
    )
    stop = urllib.parse.quote(
        (now_utc + datetime.timedelta(hours=48)).strftime(
            "%Y-%m-%d %H:00:00.000+0000"
        )
    )

    url = f"http://api.pluto.tv/v2/channels?start={start}&stop={stop}"
    logger.info("url: %s", url)
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    channels_list = response.json()
    slugs = {ch['slug']: ch for ch in channels_list if 'slug' in ch}
    categories = {}
    for ch in channels_list:
        category = ch.get('category', 'Other')
        slug = ch.get('slug')
        if slug:
            categories.setdefault(category, []).append(slug)
    # Sort each category's slug list by channel name
    for category, slug_list in categories.items():
        slug_list.sort(key=lambda s: slugs[s]['name'] if s in slugs and 'name' in slugs[s] else s)
    cache_dict = {
        'slugs': slugs,
        'categories': categories
    }
    with CHANNEL_EPG_CACHE.open("w", encoding="utf-8") as f:
        json.dump(cache_dict, f)
    logger.info("Using api.pluto.tv, writing cache.json as dict with slugs and categories.")
    return cache_dict


def fetch_json():
    """
    Fetches channel and EPG data from the PlutoTV API.

    This function retrieves the next 48 hours of programming information.
    It uses a local cache (`cache.json`) to avoid making repeated API calls
    if the cached data is less than 30 minutes old.

    Returns:
        dict: A dictionary containing the JSON response from the PlutoTV API.
    """
    logger.debug("Grabbing EPG...")

    if CACHE_FILE.exists():
        age = (
            datetime.datetime.now()
            - datetime.datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        ).total_seconds()
        if age <= 1800:
            logger.debug("Using cache.json, it's under 30 minutes old.")
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))

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
    CACHE_FILE.write_text(response.text, encoding="utf-8")
    logger.debug("Using api.pluto.tv, writing cache.json.")
    return response.json()


def build_playlist(channels):
    """
    Builds an M3U8 playlist string from a list of channel data.

    For each channel, this function generates a unique, session-specific stream
    URL by adding or updating necessary query parameters. It then formats this
    information into an `#EXTINF` line in the M3U8 format.

    Args:
        channels (list[dict]): A list of channel dictionaries from the PlutoTV API.

    Returns:
        str: A string containing the complete M3U8 playlist.
    """
    m3u8 = "#EXTM3U\n"
    for channel in channels:
        if not channel.get("isStitched"):
            logger.debug("Skipping 'fake' channel %s.", channel['name'])
            continue

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

        slug = channel["slug"]
        logo = channel["colorLogoPNG"]["path"]
        group = channel["category"]
        name = channel["name"]

        m3u8 += f'#EXTINF:0 tvg-id="{slug}" tvg-logo="{logo}" group-title="{group}", {name}\n{updated_url}\n\n'
        logger.debug("Adding %s channel.", name)
    return m3u8


def build_epg(channels):
    """
    Builds an XMLTV EPG from a list of channel data.

    This function creates an XML structure compatible with the XMLTV format.
    It generates `<channel>` elements for each channel and `<programme>` elements
    for each scheduled program, including details like title, description,
    start/stop times, and categories.

    Args:
        channels (list[dict]): A list of channel dictionaries from the PlutoTV API.

    Returns:
        xml.etree.ElementTree.ElementTree: An ElementTree object representing the EPG.
    """
    tv = ET.Element("tv")

    for channel in channels:
        if channel.get("isStitched") and channel.get("timelines"):
            for programme in channel["timelines"]:
                ep = programme.get("episode", {})
                prog_elem = ET.SubElement(
                    tv,
                    "programme",
                    {
                        "start": datetime.datetime.fromisoformat(
                            programme["start"]
                        ).strftime("%Y%m%d%H%M%S %z"),
                        "stop": datetime.datetime.fromisoformat(
                            programme["stop"]
                        ).strftime("%Y%m%d%H%M%S %z"),
                        "channel": channel["slug"],
                    },
                )

                ET.SubElement(prog_elem, "title", {"lang": "en"}).text = programme[
                    "title"
                ]
                subtitle = (
                    "" if programme["title"] == ep.get("name") else ep.get("name", "")
                )
                ET.SubElement(prog_elem, "sub-title", {"lang": "en"}).text = subtitle
                ET.SubElement(prog_elem, "desc", {"lang": "en"}).text = ep.get(
                    "description", ""
                )
                if ep.get("firstAired"):
                    ET.SubElement(prog_elem, "date").text = (
                        datetime.datetime.fromisoformat(ep["firstAired"]).strftime(
                            "%Y%m%d"
                        )
                    )
                for cat in (
                    ep.get("genre"),
                    ep.get("subGenre"),
                    ep.get("series", {}).get("type"),
                    channel["category"],
                ):
                    if cat:
                        ET.SubElement(prog_elem, "category", {"lang": "en"}).text = cat
                if ep.get("number"):
                    ET.SubElement(
                        prog_elem, "episode-num", {"system": "onscreen"}
                    ).text = str(ep["number"])
                if ep.get("poster", {}).get("path"):
                    ET.SubElement(prog_elem, "icon", {"src": ep["poster"]["path"]})

        chan_elem = ET.SubElement(tv, "channel", {"id": channel["slug"]})
        ET.SubElement(chan_elem, "display-name").text = channel["name"]
        ET.SubElement(chan_elem, "display-name").text = str(channel["number"])
        ET.SubElement(chan_elem, "desc").text = channel["summary"]
        ET.SubElement(chan_elem, "icon", {"src": channel["colorLogoPNG"]["path"]})

    return ET.ElementTree(tv)


def create_playlist_and_epg():
    """
    Main function to generate and save the PlutoTV playlist and EPG.

    This function orchestrates the process:
    1. Checks if the playlist and EPG files are already up-to-date for the day.
    2. Fetches the latest channel data from the PlutoTV API.
    3. Filters the channels based on the user's favorites file.
    4. Builds the M3U8 playlist and saves it.
    5. Builds the XMLTV EPG and saves it.
    """
    # Check if the playlist file exists and if it's from today
    if PLAYLIST_FILE.exists():
        file_mod_time = datetime.datetime.fromtimestamp(PLAYLIST_FILE.stat().st_mtime).date()
        if file_mod_time == datetime.date.today():
            logger.info("Playlist file %s is already up to date for today.", PLAYLIST_FILE)
            return

    channels = fetch_json()

    # Filter channels using favorites
    favorites_filter = from_favorites(FAVORITES_PATH)
    if favorites_filter and not favorites_filter.is_empty():
        channels = list(filter(favorites_filter, channels))
        favorites_filter.print_summary()
    else:
        logger.debug(
            "No favorites specified (%s), loading all channels.", FAVORITES_PATH
        )

    # Generate and save M3U8
    m3u8 = build_playlist(channels)
    PLAYLIST_FILE.write_text(m3u8)
    logger.info("Wrote the M3U8 playlist to %s!", PLAYLIST_FILE)

    # Generate and save XMLTV
    epg_tree = build_epg(channels)
    epg_tree.write(str(EPG_FILE), encoding="utf-8", xml_declaration=True)
    logger.info("Wrote the EPG to %s!", EPG_FILE)

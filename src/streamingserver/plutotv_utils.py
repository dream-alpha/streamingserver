"""
PlutoTV Playlist and EPG Generator

This module provides functions to fetch channel and program data from the
PlutoTV API, and then build an M3U8 playlist and an XMLTV EPG (Electronic
Program Guide) file from that data.

It includes functionality for:
- Caching the API response to avoid excessive requests.
- Filtering channels based on a user-defined favorites list.
- Generating M3U8 playlists with updated, session-specific stream URLs.
- Generating a comprehensive XMLTV EPG with detailed program information.
"""
import uuid
import json
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

from favorites import from_favorites
from debug import get_logger

logger = get_logger(__file__)

PLAYLIST_DIR = Path.home() / "plugins/streamingserver/data"
CACHE_FILE = PLAYLIST_DIR / "cache.json"
FAVORITES_PATH = PLAYLIST_DIR / "pluto-favorites"
EPG_FILE = PLAYLIST_DIR / "plutotv-epg.xml"
PLAYLIST_FILE = PLAYLIST_DIR / "plutotv-playlist.m3u8"


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
            return json.loads(CACHE_FILE.read_text())

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
    CACHE_FILE.write_text(response.text)
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

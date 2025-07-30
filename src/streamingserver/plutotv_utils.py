import uuid
import json
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

from favorites import from_favorites
from debug import get_logger

logger = get_logger(__name__, "DEBUG")

PLAYLIST_DIR = Path.home() / "plugins/streamingserver/data"
CACHE_FILE = PLAYLIST_DIR / "cache.json"
FAVORITES_PATH = PLAYLIST_DIR / "pluto-favorites"
EPG_FILE = PLAYLIST_DIR / "plutotv-epg.xml"
PLAYLIST_FILE = PLAYLIST_DIR / "plutotv-playlist.m3u8"


def fetch_json():
    logger.info("Grabbing EPG...")

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
    logger.debug(f"url: {url}")
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    CACHE_FILE.write_text(response.text)
    logger.debug("Using api.pluto.tv, writing cache.json.")
    return response.json()


def build_playlist(channels):
    m3u8 = "#EXTM3U\n"
    for channel in channels:
        if not channel.get("isStitched"):
            logger.debug(f"Skipping 'fake' channel {channel['name']}.")
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
        logger.debug(f"Adding {name} channel.")
    return m3u8


def build_epg(channels):
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
        # Fix: Convert the number to string
        ET.SubElement(chan_elem, "display-name").text = str(channel["number"])
        ET.SubElement(chan_elem, "desc").text = channel["summary"]
        ET.SubElement(chan_elem, "icon", {"src": channel["colorLogoPNG"]["path"]})

    return ET.ElementTree(tv)


def create_playlist_and_epg():
    channels = fetch_json()

    # Filter channels using favorites
    favorites_filter = from_favorites(FAVORITES_PATH)
    if favorites_filter and not favorites_filter.is_empty():
        channels = list(filter(favorites_filter, channels))
        favorites_filter.print_summary()
    else:
        logger.debug(
            f"No favorites specified ({FAVORITES_PATH}), loading all channels."
        )

    # Generate and save M3U8
    m3u8 = build_playlist(channels)
    PLAYLIST_FILE.write_text(m3u8)
    logger.debug(f"[SUCCESS] Wrote the M3U8 tuner to {PLAYLIST_FILE}!")

    # Generate and save XMLTV
    epg_tree = build_epg(channels)
    # Fix: Convert Path to string
    epg_tree.write(str(EPG_FILE), encoding="utf-8", xml_declaration=True)
    logger.debug(f"[SUCCESS] Wrote the EPG to {EPG_FILE}!")

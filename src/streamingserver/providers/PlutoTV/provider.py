# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import re
import uuid
import json
import threading
import datetime
import urllib.parse
from base_provider import BaseProvider
# from debug import get_logger

# logger = get_logger(__file__)


class Provider(BaseProvider):
    def __init__(self, args: dict):
        super().__init__(args)
        self.cache_file = self.data_dir / "cache.json"
        self._update_thread = None
        self._stop_event = threading.Event()

    def get_categories(self):
        """
        Returns a list of categories sorted alphabetically.
        """
        # logger.info("Fetching categories")
        self.update_channel_data()  # create categories_file if it doesn't exist
        categories = []
        categories_file = self.data_dir / "categories.json"
        with open(categories_file, 'r', encoding="utf-8") as f:
            categories = json.load(f)

        # Ensure categories are sorted alphabetically by name
        categories_with_provider = [{**category, "provider_id": self.provider_id} for category in categories]
        categories_with_provider.sort(key=lambda x: x['name'].lower())

        return categories_with_provider

    def get_media_items(self, category):
        """
        Returns a list of channels sorted alphabetically by title.
        """
        # logger.info("Fetching channels for category: %s", category)
        channels = []
        channels_file = self.data_dir / "channels.json"
        with open(channels_file, 'r', encoding="utf-8") as f:
            channels = json.load(f)

        media_items = channels.get(category["name"], [])
        # Sort channels alphabetically by name/title
        if media_items:
            media_items.sort(key=lambda x: x.get('name', x.get('title', '')).lower())

        return media_items

    def get_current_epg(self, channel):
        """
        Return the current EPG entry for a PlutoTV channel, or None if not found.
        """
        now = datetime.datetime.utcnow()
        timelines = channel.get("timelines")
        if timelines:
            for programme in timelines:
                # logger.info("programme: %s", programme)
                start = self.parse_iso8601(programme["start"])
                stop = self.parse_iso8601(programme["stop"])
                if start and stop and start <= now < stop:
                    return programme
        return None

    def get_all_epg(self, channel):
        """
        Return a list of all EPG entries for a PlutoTV channel.
        Each entry is a dict from the channel's 'timelines'.
        """
        # logger.info("Fetching upcoming EPG for channel: %s", channel)
        now = datetime.datetime.utcnow()
        timelines = channel.get("timelines")
        if timelines:
            upcoming = []
            for programme in timelines:
                start = self.parse_iso8601(programme["start"])
                if start and start >= now:
                    upcoming.append(programme)
            return upcoming
        return []

    # Create data files if they don't exist

    def make_valid_filename(self, s, replacement="_"):
        """
        Converts a string to a valid filename by replacing invalid characters.
        """
        # Remove or replace invalid filename characters: \ / : * ? " < > | and control chars
        s = re.sub(r'[\\/:*?"<>|\r\n\t ]', replacement, s)
        # Optionally, strip leading/trailing whitespace and dots
        s = s.strip().strip('.')
        return s

    def update_channel_data(self):
        if self._update_thread and self._update_thread.is_alive():
            return  # Already running

        def update_loop():
            while not self._stop_event.wait(1800):  # 30 minutes or until stop
                try:
                    self.create_channel_data()
                except Exception:
                    pass

        self.create_channel_data()  # Initial call
        self._update_thread = threading.Thread(target=update_loop, daemon=True)
        self._update_thread.start()

    def stop_updates(self):
        if self._stop_event:
            self._stop_event.set()

    def create_channel_data(self):
        channels = self.fetch_json()
        if not channels:
            return None, None

        categories_dict = {}
        category_names_list = []
        categories_list = []
        for channel in channels:
            channel["url"] = self.build_url(channel)
            category = channel.get('category', 'Other')
            if category not in category_names_list:
                category_names_list.append(category)
            categories_dict.setdefault(category, []).append(channel)

        for category in categories_dict:  # pylint: disable=consider-using-dict-items
            categories_dict[category].sort(key=lambda x: x['name'])

        with (self.data_dir / "channels.json").open("w", encoding="utf-8") as f:
            json.dump(categories_dict, f)

        category_names_list.sort()
        for category in category_names_list:
            categories_list.append(
                {
                    "name": category,
                    "icon": self.make_valid_filename(category) + ".png"
                }
            )
        with (self.data_dir / "categories.json").open("w", encoding="utf-8") as f:
            json.dump(categories_list, f)
        return categories_list, categories_dict

    def fetch_json(self):
        """
        Fetches channel and EPG data from the PlutoTV API.

        This function retrieves the next 48 hours of programming information.
        It uses a local cache (`cache.json`) to avoid making repeated API calls
        if the cached data is less than 30 minutes old.

        Returns:
            dict: A dictionary containing the JSON response from the PlutoTV API.
        """
        # logger.debug("Fetching channel data...")

        if self.cache_file.exists():
            age = (
                datetime.datetime.now()
                - datetime.datetime.fromtimestamp(self.cache_file.stat().st_mtime)
            ).total_seconds()
            if age <= 1800:
                # logger.info("No update required.")
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
        # logger.debug("url: %s", url)
        response = self.session.get(url, timeout=5)
        response.raise_for_status()
        # Store as pretty-printed JSON
        try:
            parsed_json = response.json()
            self.cache_file.write_text(
                json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            # logger.error(f"Failed to pretty-print JSON: {e}")
            self.cache_file.write_text(response.text, encoding="utf-8")
        # logger.debug("Using api.pluto.tv, writing cache.json.")
        return response.json()

    def build_url(self, channel):
        if not channel.get("isStitched"):
            # logger.debug("Skipping 'fake' channel %s.", channel['name'])
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

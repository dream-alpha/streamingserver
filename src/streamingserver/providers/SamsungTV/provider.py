# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import os
import re
import json
import gzip
import tempfile
from io import BytesIO
import threading
import datetime
from base_provider import BaseProvider
from debug import get_logger

logger = get_logger(__file__)


TIMEOUT = (5, 20)  # connect, read


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

    def fetch_json(self):
        if self.cache_file.exists():
            age = (
                datetime.datetime.now()
                - datetime.datetime.fromtimestamp(self.cache_file.stat().st_mtime)
            ).total_seconds()
            if age <= 1800:
                # logger.info("No update required.")
                return None
        else:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        url = 'https://i.mjh.nz/SamsungTVPlus/.channels.json.gz'
        # Prefer streaming so we can handle large responses safely
        resp = self.session.get(url, stream=True, timeout=TIMEOUT)
        resp.raise_for_status()

        # We'll stream-decompress into a temporary file to avoid large memory usage
        tmp_path = None
        try:
            # If server advertises gzip encoding, use resp.raw directly with GzipFile
            content_encoding = resp.headers.get('Content-Encoding', '')
            if content_encoding and 'gzip' in content_encoding.lower():
                gzfile = gzip.GzipFile(fileobj=resp.raw)
            else:
                # Server may serve a .gz file body without Content-Encoding; detect magic
                # Read a small prefix to check for gzip magic
                prefix = resp.raw.read(2)
                rest = resp.raw.read()
                combined = prefix + rest
                if combined[:2] == b'\x1f\x8b':
                    gzfile = gzip.GzipFile(fileobj=BytesIO(combined))
                else:
                    # Not gzipped: treat combined as plain text bytes
                    # Write to temp file and parse as text
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        tmp.write(combined)
                        tmp_path = tmp.name
                    with open(tmp_path, 'r', encoding='utf-8') as tf:
                        parsed_json = json.load(tf)
                    # cache and return
                    self.cache_file.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")
                    return parsed_json

            # If we have a gzfile object, stream its decompressed content to a temp file
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
                chunk = gzfile.read(1024 * 8)
                while chunk:
                    tmp.write(chunk)
                    chunk = gzfile.read(1024 * 8)

            # Read parsed JSON from temp file
            with open(tmp_path, 'r', encoding='utf-8') as tf:
                parsed_json = json.load(tf)

            # Store pretty-printed JSON cache as UTF-8
            self.cache_file.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")
            # Cleanup temporary file
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            # logger.info(f"Fetched and cached SamsungTV JSON to {self.cache_file}")
            return parsed_json
        except Exception:
            # Last resort: try requests.json() which may handle some cases
            try:
                parsed_json = resp.json()
                self.cache_file.write_text(json.dumps(parsed_json, ensure_ascii=False, indent=2), encoding="utf-8")
                return parsed_json
            except Exception:
                try:
                    if tmp_path:
                        # save raw response text to cache file for inspection
                        self.cache_file.write_text(resp.text, encoding="utf-8")
                except Exception:
                    pass
                logger.info("Failed to fetch or parse SamsungTV JSON")
                # Cleanup tmp file on error
                try:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return None

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
        result = self.fetch_json()
        if not result:
            return None, None

        categories_dict = {}
        category_names_list = []
        categories_list = []
        regions = result["regions"]
        for region in regions:
            if region == "de":
                channels = result["regions"][region]["channels"]
                for slug, channel in channels.items():
                    category = channel.get('group', 'Other')
                    if category not in category_names_list:
                        category_names_list.append(category)
                    channel["slug"] = slug
                    channel["url"] = f'https://jmp2.uk/stvp-{slug}'
                    categories_dict.setdefault(category, []).append(channel)

                for category in categories_dict:  # pylint: disable=consider-using-dict-items
                    categories_dict[category].sort(key=lambda x: x['name'])

                with open(self.data_dir / "channels.json", "w", encoding="utf-8") as f:
                    json.dump(categories_dict, f)

                category_names_list.sort()
                for category in category_names_list:
                    categories_list.append(
                        {
                            "name": category,
                            "icon": self.make_valid_filename(category) + ".png"
                        }
                    )
                with open(self.data_dir / "categories.json", "w", encoding="utf-8") as f:
                    json.dump(categories_list, f)
        return categories_list, categories_dict

from collections import deque
from debug import get_logger
from hls_segment import HLSSegment

logger = get_logger(__file__)

MAX_DEDUP_WINDOW_SIZE = 500  # Arbitrary limit for deduplication window size


class HLSPlaylistProcessor:
    def __init__(self):
        self.last_playlist = []  # list of (seq, uri)
        self.dedup_window = deque(
            maxlen=MAX_DEDUP_WINDOW_SIZE
        )  # will be set in parse_playlist
        self.current_sequence = None
        self.current_discontinuity_sequence = 0
        self.reset_dedup = False
        self.playlist_type = None
        self.encryption_key = None  # simple store of last #EXT-X-KEY line
        self.endlist_seen = False
        # HLS specification tracking
        self.last_playlist_type = None
        self.last_endlist_seen = False
        self.target_duration = None
        self.last_target_duration = None

    def count_extinf_tags(self, playlist_lines) -> int:
        # Handle case where playlist_lines is a single string
        if isinstance(playlist_lines, str):
            playlist_lines = playlist_lines.strip().split("\n")

        return sum(1 for line in playlist_lines if line.strip().startswith("#EXTINF"))

    def parse_playlist(self, lines):
        playlist = []

        media_sequence = None
        sequence = 0
        discontinuity_next = False
        playlist_type = None
        encryption_key = None
        endlist_seen = False
        discontinuity_next = False

        byte_range = None
        current_encryption_info = {"METHOD": None, "URI": None, "IV": None}
        current_segment_duration = 0.0

        for line in lines:
            line = line.strip()
            if not line or not line.startswith("#"):
                if self.reset_dedup:
                    logger.debug(
                        ">> Major discontinuity detected, clearing deduplication window (had %s items)",
                        len(self.dedup_window),
                    )
                    self.dedup_window.clear()
                    self.reset_dedup = False

                if discontinuity_next:
                    logger.debug(
                        ">> Content discontinuity detected, but keeping deduplication window"
                    )

                segment = HLSSegment(
                    uri=line,
                    duration=int(current_segment_duration * 90000),
                    targetduration=self.target_duration,
                    sequence=sequence,
                    discontinuity=discontinuity_next,
                    endlist=endlist_seen,
                    key_info=current_encryption_info.copy(),
                    byte_range=byte_range,
                )
                playlist.append(segment)
                discontinuity_next = False
                sequence += 1
                current_segment_duration = 0.0
                continue

            if ":" not in line:
                if line.strip() == "#" or line.strip().startswith("# "):
                    continue
                tag = line.strip()
            else:
                tag = line.split(":", 1)[0]

            match tag:
                case "#EXT-X-MEDIA-SEQUENCE":
                    media_sequence = int(line.split(":", 1)[1])
                    sequence = media_sequence
                    logger.debug("Tag: MEDIA-SEQUENCE = %s", media_sequence)
                case "#EXT-X-DISCONTINUITY-SEQUENCE":
                    discontinuity_sequence = int(line.split(":", 1)[1])
                    logger.debug("Tag: DISCONTINUITY-SEQUENCE = %s", discontinuity_sequence)
                    if discontinuity_sequence != self.current_discontinuity_sequence:
                        logger.debug(
                            ">> Discontinuity sequence changed: %s -> %s",
                            self.current_discontinuity_sequence,
                            discontinuity_sequence,
                        )
                        if discontinuity_sequence < self.current_discontinuity_sequence:
                            logger.debug(
                                ">> HLS RULE: Discontinuity sequence decreased - stream restart detected, resetting deduplication"
                            )
                            self.reset_dedup = True
                        elif (
                            discontinuity_sequence
                            > self.current_discontinuity_sequence + 5
                        ):
                            logger.debug(
                                ">> HLS RULE: Large discontinuity sequence jump - major stream change detected, resetting deduplication"
                            )
                            self.reset_dedup = True
                        else:
                            logger.debug(
                                ">> HLS: Normal discontinuity sequence progression, keeping deduplication window"
                            )
                        self.current_discontinuity_sequence = discontinuity_sequence
                case "#EXT-X-PLAYLIST-TYPE":
                    playlist_type = line.split(":", 1)[1]
                    logger.debug("Tag: PLAYLIST-TYPE = %s", playlist_type)
                case "#EXT-X-KEY":
                    encryption_key = line
                    logger.debug("Tag: KEY = %s", encryption_key)
                    if line.startswith("#EXT-X-KEY:"):
                        key_data = line[len("#EXT-X-KEY:") :]
                        parsed_key_info = {}
                        in_quotes = False
                        current_attr = ""
                        for char in (key_data + ","):
                            if char == '"':
                                in_quotes = not in_quotes
                                current_attr += char
                            elif char == "," and not in_quotes:
                                if "=" in current_attr:
                                    key, value = current_attr.split("=", 1)
                                    key = key.strip()
                                    value = value.strip().strip('"')
                                    parsed_key_info[key] = value
                                current_attr = ""
                            else:
                                current_attr += char
                        current_encryption_info.update(parsed_key_info)
                case "#EXTINF":
                    try:
                        duration_str = line.split(":", 1)[1].split(",")[0]
                        current_segment_duration = float(duration_str)
                    except Exception:
                        current_segment_duration = 0.0
                    logger.debug("Tag: INF (segment duration line) duration=%s", current_segment_duration)
                case "#EXT-X-PART-INF" | "#EXT-X-PART" | "#EXT-X-PRELOAD-HINT":
                    logger.debug("Tag: Low-latency segment related tag: %s", tag)
                case "#EXT-X-BYTERANGE":
                    byte_range = line
                    logger.debug("Tag: BYTERANGE = %s", byte_range)
                case "#EXT-X-PROGRAM-DATE-TIME":
                    logger.debug("Tag: PROGRAM-DATE-TIME = %s", line.split(":", 1)[1])
                case "#EXT-X-CUE-OUT" | "#EXT-X-CUE-IN":
                    logger.debug("Tag: CUE marker = %s", tag)
                case "#EXT-X-DATERANGE":
                    logger.debug("Tag: DATERANGE = %s", line)
                case "#EXTM3U":
                    logger.debug("Tag: M3U header")
                case "#EXT-X-VERSION":
                    version = line.split(":", 1)[1] if ":" in line else "unknown"
                    logger.debug("Tag: VERSION = %s", version)
                case "#EXT-X-TARGETDURATION":
                    target_duration = (line.split(":", 1)[1] if ":" in line else "unknown")
                    logger.debug("Tag: TARGETDURATION = %s", target_duration)
                    try:
                        self.target_duration = int(target_duration) if target_duration != "unknown" else None
                    except ValueError:
                        self.target_duration = None
                case "#EXT-X-ALLOW-CACHE":
                    allow_cache = line.split(":", 1)[1] if ":" in line else "unknown"
                    logger.debug("Tag: ALLOW-CACHE = %s", allow_cache)
                case "#EXT-X-MEDIA":
                    media_info = line.split(":", 1)[1] if ":" in line else ""
                    logger.debug("Tag: MEDIA = %s...", media_info[:50])
                case "#EXT-X-STREAM-INF":
                    stream_info = line.split(":", 1)[1] if ":" in line else ""
                    logger.debug("Tag: STREAM-INF = %s...", stream_info[:50])
                case "#PLUTO-SESSION-ID":
                    session_id = line.split(":", 1)[1] if ":" in line else ""
                    logger.debug("Tag: PLUTO-SESSION-ID = %s...", session_id[:20])
                case "#PLUTO-VERSION":
                    pluto_version = line.split(":", 1)[1] if ":" in line else ""
                    logger.debug("Tag: PLUTO-VERSION = %s", pluto_version)
                case "#EXT-X-ENDLIST":
                    endlist_seen = True
                    logger.debug("Tag: ENDLIST")
                case "#EXT-X-DISCONTINUITY":
                    discontinuity_next = True
                    logger.debug("Tag: DISCONTINUITY (content boundary, not resetting deduplication)")
                case _:
                    if tag.strip():
                        line_display = line[:50] + "..." if len(line) > 50 else line
                        logger.debug("Tag: Unhandled tag: '%s' (from line: '%s')", tag, line_display)
                    else:
                        line_display = line[:50] + "..." if len(line) > 50 else line
                        logger.debug("Tag: Empty or malformed tag from line: '%s'", line_display)

        if media_sequence is not None:
            self.current_sequence = media_sequence
        if playlist_type is not None:
            self.playlist_type = playlist_type
        if encryption_key is not None:
            self.encryption_key = encryption_key
        if endlist_seen:
            self.endlist_seen = True

        return media_sequence, playlist

    def diff_playlist(self, new_playlist):
        diff = []
        for segment in new_playlist:
            if segment.uri not in self.dedup_window:
                diff.append(segment)
                self.dedup_window.append(segment.uri)
        return diff

    def process(self, playlist_lines):
        if not playlist_lines:
            return []

        if isinstance(playlist_lines, str):
            playlist_lines = playlist_lines.strip().split("\n")

        logger.debug(">> Processing playlist with %s lines", len(playlist_lines))
        if len(playlist_lines) == 0:
            logger.debug(">> Empty playlist")

        extinf_count = self.count_extinf_tags(playlist_lines)
        if extinf_count > 0:
            optimal_window_size = min(extinf_count, MAX_DEDUP_WINDOW_SIZE)
            if self.dedup_window.maxlen != optimal_window_size:
                existing_items = list(self.dedup_window)
                self.dedup_window = deque(existing_items, maxlen=optimal_window_size)

        try:
            media_sequence, new_playlist = self.parse_playlist(playlist_lines)
        except Exception as e:
            logger.debug("[WARNING] Playlist parsing error: %s. Skipping playlist.", e)
            return []

        if media_sequence is None:
            logger.debug("[WARNING] Missing #EXT-X-MEDIA-SEQUENCE in playlist. Skipping playlist.")
            return []

        dedup_was_cleared = False

        if self.current_sequence is not None and media_sequence is not None:
            sequence_diff = media_sequence - self.current_sequence
            if sequence_diff < 0:
                logger.debug(">> HLS RULE: Media sequence decreased %s -> %s (stream restart), resetting deduplication", self.current_sequence, media_sequence)
                self.dedup_window.clear()
                dedup_was_cleared = True
            max_jump = 15
            if self.target_duration:
                max_jump = max(15, self.target_duration * 3)
            if sequence_diff > max_jump:
                logger.debug(">> HLS RULE: Large media sequence jump %s -> %s (exceeds %s segments), resetting deduplication", self.current_sequence, media_sequence, max_jump)
                self.dedup_window.clear()
                dedup_was_cleared = True
            elif sequence_diff > 5:
                logger.debug(">> HLS: Notable media sequence jump %s -> %s (+%s) but within threshold", self.current_sequence, media_sequence, sequence_diff)
            elif sequence_diff > 0:
                logger.debug(">> HLS: Normal media sequence progression %s -> %s (+%s)", self.current_sequence, media_sequence, sequence_diff)
            elif sequence_diff == 0:
                logger.debug(">> HLS: Same media sequence %s (normal for live stream updates)", media_sequence)
        if self.playlist_type != self.last_playlist_type:
            if self.last_playlist_type is not None:
                logger.debug(">> HLS RULE: Playlist type changed %s -> %s, resetting deduplication", self.last_playlist_type, self.playlist_type)
                self.dedup_window.clear()
                dedup_was_cleared = True
        self.last_playlist_type = self.playlist_type
        if self.last_endlist_seen and not self.endlist_seen:
            logger.debug(">> HLS RULE: ENDLIST removed (stream restart), resetting deduplication")
            self.dedup_window.clear()
            dedup_was_cleared = True
        self.last_endlist_seen = self.endlist_seen
        if self.target_duration != self.last_target_duration:
            if self.last_target_duration is not None:
                logger.debug(">> HLS RULE: Target duration changed %s -> %s, resetting deduplication", self.last_target_duration, self.target_duration)
                self.dedup_window.clear()
                dedup_was_cleared = True
        self.last_target_duration = self.target_duration
        if dedup_was_cleared:
            for segment in new_playlist:
                if segment.uri not in self.dedup_window:
                    self.dedup_window.append(segment.uri)

        if not self.last_playlist:
            result = list(new_playlist)
            for segment in new_playlist:
                if segment.uri not in self.dedup_window:
                    self.dedup_window.append(segment.uri)
        else:
            diff = self.diff_playlist(new_playlist)
            result = list(diff)

        self.last_playlist = new_playlist
        self.current_sequence = media_sequence
        return result

    def get_current_encryption_info(self):
        """
        Returns the current encryption information parsed from the playlist.

        Returns:
            dict: Dictionary containing encryption information with keys:
                  - METHOD: Encryption method (e.g., 'AES-128' or None)
                  - URI: Key URI (if present)
                  - IV: IV value (if present)
        """
        if not self.encryption_key:
            return {"METHOD": None, "URI": None, "IV": None}

        # Parse the encryption key line
        # Format: #EXT-X-KEY:METHOD=AES-128,URI="...",IV=0x...
        logger.debug("Parsing encryption key: %s", self.encryption_key)
        if self.encryption_key.startswith("#EXT-X-KEY:"):
            key_data = self.encryption_key[len("#EXT-X-KEY:"):]

            # Simple attribute parsing (similar to parse_attributes)
            key_info = {}

            # Split by comma, but be careful about quoted values
            in_quotes = False
            current_attr = ""

            for char in key_data + ",":  # Add comma to process last attribute
                if char == '"':
                    in_quotes = not in_quotes
                    current_attr += char
                elif char == "," and not in_quotes:
                    if "=" in current_attr:
                        key, value = current_attr.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"')  # Remove quotes
                        key_info[key] = value
                    current_attr = ""
                else:
                    current_attr += char

            return key_info

        return {"METHOD": None, "URI": None, "IV": None}


if __name__ == "__main__":
    playlist1 = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:6",
        "#EXT-X-MEDIA-SEQUENCE:100",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        '#EXT-X-KEY:METHOD=AES-128,URI="key.key"',
        "#EXTINF:6.0,",
        "segment100.ts",
        "#EXTINF:6.0,",
        "segment101.ts",
        "#EXTINF:6.0,",
        "segment102.ts",
    ]

    # First subplaylist with MEDIA-SEQUENCE 101
    subplaylist_a = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-MEDIA-SEQUENCE:101",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXTINF:6.0,",
        "segment101.ts",
        "#EXTINF:6.0,",
        "segment102.ts",
        "#EXTINF:6.0,",
        "segment103.ts",
    ]

    # Second subplaylist with same MEDIA-SEQUENCE 101 but a slight difference (e.g. missing segment102.ts)
    subplaylist_b = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-MEDIA-SEQUENCE:101",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXTINF:6.0,",
        "segment101.ts",
        "#EXTINF:6.0,",
        "segment103.ts",  # segment102.ts missing here compared to subplaylist_a
        "#EXTINF:6.0,",
        "segment104.ts",
    ]

    # Third subplaylist, new media sequence
    playlist3 = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-MEDIA-SEQUENCE:104",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXTINF:6.0,",
        "segment103.ts",
        "#EXTINF:6.0,",
        "segment104.ts",
        "#EXTINF:6.0,",
        "segment105.ts",
        "#EXT-X-ENDLIST",
    ]

    processor = HLSPlaylistProcessor()

    logger.info("\nProcessing playlist 1:")
    logger.info(processor.process(playlist1))

    logger.info("Processing first subplaylist with sequence 101:")
    logger.info(processor.process(subplaylist_a))

    logger.info(
        "\nProcessing second subplaylist with same sequence 101 (differs slightly):"
    )
    logger.info(processor.process(subplaylist_b))

    logger.info("Processing playlist 3 with sequence 104:")
    logger.info(processor.process(playlist3))

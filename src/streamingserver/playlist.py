from collections import deque
from datetime import datetime

MAX_DEDUP_WINDOW_SIZE = 500  # Arbitrary limit for deduplication window size

def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class HLSPlaylistProcessor:
    def __init__(self):
        self.last_playlist = []  # list of (seq, uri)
        self.dedup_window = deque(maxlen=MAX_DEDUP_WINDOW_SIZE)  # will be set in parse_playlist
        self.current_sequence = None
        self.current_discontinuity_sequence = 0
        self.reset_dedup = False
        self.playlist_type = None
        self.encryption_key = None  # simple store of last #EXT-X-KEY line
        self.endlist_seen = False

    def count_extinf_tags(self, playlist_lines) -> int:
        """
        Counts the number of #EXTINF tags in a given playlist.
        
        Parameters:
            playlist_lines: List of lines from an HLS playlist (.m3u8) or a single string

        Returns:
            Number of #EXTINF entries (i.e., media segments).
        """
        # Handle case where playlist_lines is a single string
        if isinstance(playlist_lines, str):
            playlist_lines = playlist_lines.strip().split('\n')
            
        return sum(1 for line in playlist_lines if line.strip().startswith("#EXTINF"))

    def parse_playlist(self, lines):
        playlist = []

        media_sequence = None
        sequence = None
        discontinuity_next = False
        playlist_type = None
        encryption_key = None
        endlist_seen = False

        byte_range = None
        current_encryption_info = {
            "METHOD": None,
            "URI": None,
            "IV": None
        }

        for line in lines:
            line = line.strip()
            if not line or not line.startswith("#"):
                # Only reset deduplication for major discontinuities (sequence changes), not content boundaries
                if self.reset_dedup:
                    print(f"{now()} >> Major discontinuity detected, clearing deduplication window (had {len(self.dedup_window)} items)")
                    self.dedup_window.clear()
                    self.reset_dedup = False
                
                # Simple discontinuities (content boundaries) don't reset deduplication
                if discontinuity_next:
                    print(f"{now()} >> Content discontinuity detected, but keeping deduplication window")
                    discontinuity_next = False

                if sequence is None:
                    sequence = 0
                # Store segment with its encryption info
                playlist.append((sequence, line, current_encryption_info.copy()))
                sequence += 1
                continue

            # Handle lines that are just comments (# without colon)
            if ":" not in line:
                if line.strip() == "#" or line.strip().startswith("# "):
                    # Skip comment lines
                    continue
                else:
                    # This might be a tag without parameters (like #EXT-X-ENDLIST)
                    tag = line.strip()
            else:
                tag = line.split(":", 1)[0]

            match tag:
                case "#EXT-X-MEDIA-SEQUENCE":
                    media_sequence = int(line.split(":",1)[1])
                    sequence = media_sequence
                    print(f"{now()} Tag: MEDIA-SEQUENCE = {media_sequence}")

                case "#EXT-X-DISCONTINUITY-SEQUENCE":
                    discontinuity_sequence = int(line.split(":",1)[1])
                    print(f"{now()} Tag: DISCONTINUITY-SEQUENCE = {discontinuity_sequence}")
                    if discontinuity_sequence != self.current_discontinuity_sequence:
                        print(f"{now()} >> Discontinuity sequence changed: {self.current_discontinuity_sequence} -> {discontinuity_sequence}")
                        
                        # Only reset deduplication for backwards jumps (stream restarts) or large jumps
                        if discontinuity_sequence < self.current_discontinuity_sequence:
                            print(f"{now()} >> WARNING: Discontinuity sequence decreased - stream restart detected, resetting deduplication")
                            self.reset_dedup = True
                        elif discontinuity_sequence > self.current_discontinuity_sequence + 10:
                            print(f"{now()} >> WARNING: Large discontinuity sequence jump - major stream change detected, resetting deduplication")
                            self.reset_dedup = True
                        else:
                            print(f"{now()} >> Normal content transition, keeping deduplication window")
                        
                        self.current_discontinuity_sequence = discontinuity_sequence
                        # Note: dedup_window.clear() will be called only if reset_dedup is True

                case "#EXT-X-PLAYLIST-TYPE":
                    playlist_type = line.split(":",1)[1]
                    print(f"{now()} Tag: PLAYLIST-TYPE = {playlist_type}")

                case "#EXT-X-KEY":
                    encryption_key = line
                    print(f"{now()} Tag: KEY = {encryption_key}")
                    
                    # Parse and update current encryption info
                    if line.startswith("#EXT-X-KEY:"):
                        key_data = line[len("#EXT-X-KEY:"):]
                        
                        # Parse attributes
                        parsed_key_info = {}
                        in_quotes = False
                        current_attr = ""
                        
                        for char in key_data + ",":  # Add comma to process last attribute
                            if char == '"':
                                in_quotes = not in_quotes
                                current_attr += char
                            elif char == ',' and not in_quotes:
                                if '=' in current_attr:
                                    key, value = current_attr.split('=', 1)
                                    key = key.strip()
                                    value = value.strip().strip('"')  # Remove quotes
                                    parsed_key_info[key] = value
                                current_attr = ""
                            else:
                                current_attr += char
                        
                        # Update current encryption info
                        current_encryption_info.update(parsed_key_info)

                case "#EXTINF":
                    print(f"{now()} Tag: INF (segment duration line)")

                case "#EXT-X-PART-INF" | "#EXT-X-PART" | "#EXT-X-PRELOAD-HINT":
                    print(f"{now()} Tag: Low-latency segment related tag: {tag}")

                case "#EXT-X-BYTERANGE":
                    byte_range = line
                    print(f"{now()} Tag: BYTERANGE = {byte_range}")

                case "#EXT-X-PROGRAM-DATE-TIME":
                    print(f"{now()} Tag: PROGRAM-DATE-TIME = {line.split(':',1)[1]}")

                case "#EXT-X-CUE-OUT" | "#EXT-X-CUE-IN":
                    print(f"{now()} Tag: CUE marker = {tag}")

                case "#EXT-X-DATERANGE":
                    print(f"{now()} Tag: DATERANGE = {line}")

                case "#EXTM3U":
                    print(f"{now()} Tag: M3U header")

                case "#EXT-X-VERSION":
                    version = line.split(":",1)[1] if ":" in line else "unknown"
                    print(f"{now()} Tag: VERSION = {version}")

                case "#EXT-X-TARGETDURATION":
                    target_duration = line.split(":",1)[1] if ":" in line else "unknown"
                    print(f"{now()} Tag: TARGETDURATION = {target_duration}")

                case "#EXT-X-ALLOW-CACHE":
                    allow_cache = line.split(":",1)[1] if ":" in line else "unknown"
                    print(f"{now()} Tag: ALLOW-CACHE = {allow_cache}")

                case "#EXT-X-MEDIA":
                    media_info = line.split(":",1)[1] if ":" in line else ""
                    print(f"{now()} Tag: MEDIA = {media_info[:50]}...")

                case "#EXT-X-STREAM-INF":
                    stream_info = line.split(":",1)[1] if ":" in line else ""
                    print(f"{now()} Tag: STREAM-INF = {stream_info[:50]}...")

                case "#PLUTO-SESSION-ID":
                    session_id = line.split(":",1)[1] if ":" in line else ""
                    print(f"{now()} Tag: PLUTO-SESSION-ID = {session_id[:20]}...")

                case "#PLUTO-VERSION":
                    pluto_version = line.split(":",1)[1] if ":" in line else ""
                    print(f"{now()} Tag: PLUTO-VERSION = {pluto_version}")

                case "#EXT-X-ENDLIST":
                    endlist_seen = True
                    print(f"{now()} Tag: ENDLIST")

                case "#EXT-X-DISCONTINUITY":
                    discontinuity_next = True
                    # Note: We don't automatically increment discontinuity sequence or reset deduplication
                    # for simple discontinuities - only do that for major stream changes
                    print(f"{now()} Tag: DISCONTINUITY (content boundary, not resetting deduplication)")

                case _:
                    # Better debugging for unhandled tags
                    if tag.strip():
                        line_display = line[:50] + '...' if len(line) > 50 else line
                        print(f"{now()} Tag: Unhandled tag: '{tag}' (from line: '{line_display}')")
                    else:
                        line_display = line[:50] + '...' if len(line) > 50 else line
                        print(f"{now()} Tag: Empty or malformed tag from line: '{line_display}'")

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
        for _, uri, encryption_info in new_playlist:
            if uri not in self.dedup_window:
                diff.append((uri, encryption_info))
                self.dedup_window.append(uri)
        return diff

    def process(self, playlist_lines):
        """
        Process an HLS playlist and return new segments with their encryption information.
        
        Parameters:
            playlist_lines: List of lines from an HLS playlist (.m3u8) or a single string
            
        Returns:
            List of tuples: [(uri, encryption_info), ...] where:
                - uri: String URL of the media segment
                - encryption_info: Dict with keys 'METHOD', 'URI', 'IV' for encryption
        """
        if not playlist_lines:
            return []
        
        # Handle case where playlist_lines is a single string instead of list of lines
        if isinstance(playlist_lines, str):
            playlist_lines = playlist_lines.strip().split('\n')
        
        # Debug: Show first few lines of the playlist
        print(f"{now()} >> Processing playlist with {len(playlist_lines)} lines")
        if len(playlist_lines) > 0:
            print(f"{now()} >> First 5 lines: {[line[:50] + '...' if len(line) > 50 else line for line in playlist_lines[:5]]}")
        else:
            print(f"{now()} >> Empty playlist")
        
        # Dynamically size the dedup window based on playlist content
        extinf_count = self.count_extinf_tags(playlist_lines)
        if extinf_count > 0:
            # Use the smaller of extinf_count or MAX_DEDUP_WINDOW_SIZE
            optimal_window_size = min(extinf_count, MAX_DEDUP_WINDOW_SIZE)
            if self.dedup_window.maxlen != optimal_window_size:
                # Preserve existing items when resizing
                existing_items = list(self.dedup_window)
                self.dedup_window = deque(existing_items, maxlen=optimal_window_size)
            
        media_sequence, new_playlist = self.parse_playlist(playlist_lines)

        if media_sequence is None:
            raise ValueError("Missing #EXT-X-MEDIA-SEQUENCE in playlist")

        if not self.last_playlist:
            result = [(uri, encryption_info) for _, uri, encryption_info in new_playlist]
            # Use the same logic as diff_playlist for consistency
            for _, uri, encryption_info in new_playlist:
                if uri not in self.dedup_window:
                    self.dedup_window.append(uri)
        else:
            result = self.diff_playlist(new_playlist)

        self.last_playlist = new_playlist
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
            return {
                "METHOD": None,
                "URI": None,
                "IV": None
            }
        
        # Parse the encryption key line
        # Format: #EXT-X-KEY:METHOD=AES-128,URI="...",IV=0x...
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
                elif char == ',' and not in_quotes:
                    if '=' in current_attr:
                        key, value = current_attr.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"')  # Remove quotes
                        key_info[key] = value
                    current_attr = ""
                else:
                    current_attr += char
            
            return key_info
        
        return {
            "METHOD": None,
            "URI": None,
            "IV": None
        }

if __name__ == "__main__":
    playlist1 = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:6",
        "#EXT-X-MEDIA-SEQUENCE:100",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-KEY:METHOD=AES-128,URI=\"key.key\"",
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

    print(f"{now()} Processing playlist 1:")
    print(processor.process(playlist1))

    print(f"\n{now()} Processing first subplaylist with sequence 101:")
    print(processor.process(subplaylist_a))

    print(f"\n{now()} Processing second subplaylist with same sequence 101 (differs slightly):")
    print(processor.process(subplaylist_b))

    print(f"\n{now()} Processing playlist 3 with sequence 104:")
    print(processor.process(playlist3))

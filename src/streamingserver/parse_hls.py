def parse_hls_playlist(lines):
    """
    Parses an HLS playlist line by line using match-case on keywords.
    
    Args:
        lines (list[str]): Lines from an HLS playlist file.
        
    Returns:
        dict: Parsed playlist data with keys:
            - segments: list of dicts with 'duration', 'title', 'uri', 'key' info
            - target_duration: int or None
            - current_key: dict or None
    """
    playlist = {
        "segments": [],
        "target_duration": None,
        "current_key": None,
    }
    
    current_segment = {}
    
    for line in lines:
        line = line.strip()
        
        if not line:
            continue
        
        if not line.startswith("#"):
            # URI line
            if current_segment:
                current_segment["uri"] = line
                playlist["segments"].append(current_segment)
                current_segment = {}
            else:
                playlist["segments"].append({"uri": line})
            continue
        
        # line starts with #
        # Extract tag keyword (up to first colon or end)
        keyword = line[1:].split(":", 1)[0]
        data = line[len(keyword)+2:] if ":" in line else ""
        
        match keyword:
            case "EXTINF":
                try:
                    duration_str, *title_parts = data.split(",", 1)
                    duration = float(duration_str)
                    title = title_parts[0] if title_parts else None
                    current_segment = {"duration": duration, "title": title}
                except Exception as e:
                    print(f"Failed to parse EXTINF: {line}, error: {e}")
                    current_segment = {}
            
            case "EXT-X-TARGETDURATION":
                try:
                    playlist["target_duration"] = int(data)
                except Exception as e:
                    print(f"Failed to parse TARGETDURATION: {line}, error: {e}")
            
            case "EXT-X-KEY":
                attrs = parse_attributes(data)
                playlist["current_key"] = attrs
            
            case _:
                # Other tags can be handled here if needed
                pass
    
    return playlist


def parse_attributes(attr_str):
    """
    Parses attribute string of form: key1=value1,key2="value2",...
    
    Returns dict of key:value pairs with quotes stripped.
    """
    attrs = {}
    import re
    pattern = re.compile(r'''([A-Z0-9\-]+)=(".*?"|[^",]*)''')
    for match in pattern.finditer(attr_str):
        key, val = match.groups()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        attrs[key] = val
    return attrs


# Example usage
if __name__ == "__main__":
    playlist_lines = [
        "#EXTM3U",
        "#EXT-X-TARGETDURATION:10",
        "#EXT-X-KEY:METHOD=AES-128,URI=\"https://example.com/key.key\"",
        "#EXTINF:9.984,",
        "segment1.ts",
        "#EXTINF:9.984,",
        "segment2.ts",
        "#EXT-X-KEY:METHOD=NONE",
        "#EXTINF:9.984,",
        "segment3.ts",
        "#EXT-X-DISCONTINUITY",
        "#EXT-X-ENDLIST",
    ]
    
    import pprint
    pprint.pprint(parse_hls_playlist(playlist_lines))


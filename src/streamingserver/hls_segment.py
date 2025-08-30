from dataclasses import dataclass, field


@dataclass
class HLSSegment:
    """Class for storing segment information with all segment-specific metadata"""
    uri: str = ""
    duration: int = 0
    targetduration: int = 0
    sequence: int = 0
    discontinuity: bool = False
    endlist: bool = False
    key_info: dict = field(default_factory=dict)
    byte_range: str = ""
    program_date_time: str = ""  # EXT-X-PROGRAM-DATE-TIME
    map_info: dict = field(default_factory=dict)  # EXT-X-MAP
    cue_out: bool = False  # Beginning of Ad marker
    cue_in: bool = False  # End of ad marker
    bitrate: int = 0  # Bitrate info if available
    title: str = ""  # Title from EXTINF

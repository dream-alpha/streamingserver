import xml.etree.ElementTree as ET
import datetime
import time
from debug import get_logger

logger = get_logger(__name__, "DEBUG")


def extract_epg_for_channel(xmltv_file_path, channel_id):
    """
    Extract all program entries for a specific channel from an XMLTV file.

    Args:
        xmltv_file_path (str): Path to the XMLTV file
        channel_id (str): The channel ID to extract programs for (tvg-id)

    Returns:
        dict: A dictionary with 'channel_info' and 'programs' keys
    """
    try:
        # Parse the XML file
        logger.info(f"Parsing XMLTV file: {xmltv_file_path}")
        tree = ET.parse(xmltv_file_path)
        root = tree.getroot()

        # Find channel information
        channel_info = {}
        for channel in root.findall(".//channel"):
            if channel.get("id") == channel_id:
                channel_info = {
                    "id": channel_id,
                    "display_name": channel.findtext(".//display-name", ""),
                    "icon": (
                        channel.find(".//icon").get("src", "")
                        if channel.find(".//icon") is not None
                        else ""
                    ),
                }
                break

        # Find all programs for the channel
        programs = []
        for program in root.findall(".//programme"):
            if program.get("channel") == channel_id:
                # Parse start and end times
                start_time = parse_xmltv_time(program.get("start", ""))
                end_time = parse_xmltv_time(program.get("stop", ""))

                # Calculate duration in seconds
                duration = end_time - start_time if end_time > start_time else 0

                # Extract program details
                title = program.findtext(".//title", "")
                subtitle = program.findtext(".//sub-title", "")
                desc = program.findtext(".//desc", "")

                # Get categories if available
                categories = [
                    category.text
                    for category in program.findall(".//category")
                    if category.text
                ]

                # Get episode info if available
                episode_num = program.findtext(".//episode-num", "")

                programs.append(
                    {
                        "title": title,
                        "subtitle": subtitle,
                        "description": desc,
                        "start_time": start_time,
                        "end_time": end_time,
                        "duration": duration,
                        "categories": categories,
                        "episode_num": episode_num,
                    }
                )

        # Sort programs by start time
        programs.sort(key=lambda x: x["start_time"])

        logger.info(f"Found {len(programs)} programs for channel {channel_id}")
        return {"channel_info": channel_info, "programs": programs}

    except Exception as e:
        logger.error(f"Error extracting EPG data: {str(e)}")
        return {"channel_info": {}, "programs": []}


def parse_xmltv_time(time_str):
    """
    Parse XMLTV time format (YYYYMMDDHHMMSS +HHMM) to Unix timestamp.

    Args:
        time_str (str): Time string in XMLTV format

    Returns:
        int: Unix timestamp (seconds since epoch)
    """
    if not time_str:
        return 0

    try:
        # Split the time string into date+time and timezone
        datetime_part = time_str.split()[0]

        # Parse the date+time part
        year = int(datetime_part[0:4])
        month = int(datetime_part[4:6])
        day = int(datetime_part[6:8])
        hour = int(datetime_part[8:10])
        minute = int(datetime_part[10:12])
        second = int(datetime_part[12:14]) if len(datetime_part) >= 14 else 0

        # Create datetime object and convert to timestamp
        dt = datetime.datetime(year, month, day, hour, minute, second)
        return int(dt.timestamp())

    except Exception as e:
        logger.error(f"Error parsing XMLTV time '{time_str}': {str(e)}")
        return 0


def get_current_program(programs):
    """
    Find the currently airing program from a list of programs.

    Args:
        programs (list): List of program dictionaries

    Returns:
        dict: The current program or None if not found
    """
    now = int(time.time())

    for program in programs:
        if program["start_time"] <= now < program["end_time"]:
            return program

    return None


def get_upcoming_programs(programs, limit=10):
    """
    Get a list of upcoming programs.

    Args:
        programs (list): List of program dictionaries
        limit (int): Maximum number of programs to return

    Returns:
        list: List of upcoming programs
    """
    now = int(time.time())
    upcoming = [p for p in programs if p["start_time"] > now]
    return upcoming[:limit]

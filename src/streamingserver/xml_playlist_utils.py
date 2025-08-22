import xml.etree.ElementTree as ET
from debug import get_logger

logger = get_logger(__file__)


def get_playlist(xml_file):
    # Parse the XML file
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()

        channels = []
        for channel in root.findall('.//channel'):
            channel_id = channel.get('id')
            display_name = None
            icon_src = None
            for child in channel:
                if child.tag == 'display-name':
                    display_name = child.text
                elif child.tag == 'icon':
                    icon_src = child.get('src')
            if display_name.startswith('Pluto TV'):
                display_name = display_name.replace('Pluto TV', '', 1).strip()
            channels.append({
                'channel_id': channel_id,
                'display_name': display_name,
                'icon_src': icon_src
            })

    except Exception as e:
        logger.debug("❌ Error reading XML file %s: %s", xml_file, e)
        return []
    # Sort channels by display_name (case-insensitive, None last)
    channels.sort(key=lambda c: (c['display_name'] is None, (c['display_name'] or '').lower()))
    return channels

import xml.etree.ElementTree as ET
import json

# Parse the XML file
tree = ET.parse('de.xml')
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
    channels.append({
        'channel_id': channel_id,
        'display_name': display_name,
        'icon_src': icon_src
    })

# Write the list to de.json
with open('de.json', 'w', encoding='utf-8') as f:
    json.dump(channels, f, ensure_ascii=False, indent=2)

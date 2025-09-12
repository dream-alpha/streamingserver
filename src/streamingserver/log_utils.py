
import os
try:
    from Version import ID
except Exception:
    from version import ID
from datetime import datetime

LOG_FILENAME = "stream.log"

def write_log(rec_dir, current_uri, section_index, segment_index, msg="none"):
    log_file = rec_dir + "/" + LOG_FILENAME
    # Get current time in hh:mm:ss.milliseconds format
    now = datetime.now()
    ms = int(now.microsecond / 1000)
    timestamp = "%s.%03d" % (now.strftime("%H:%M:%S"), ms)

    try:
        section_index = "%03d" % int(section_index)
    except (ValueError, TypeError):
        section_index = "---"
    try:
        segment_index = "%03d" % int(segment_index)
    except (ValueError, TypeError):
        segment_index = "---"

    with open(log_file, 'a') as log_f:
        log_f.write("%s %s %s/%s: %s - %s\n" % (timestamp, ID, section_index, segment_index, current_uri, msg))
        log_f.flush()

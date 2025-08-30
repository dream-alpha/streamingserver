
import os
from Version import ID
from datetime import datetime


def write_log(rec_file, current_uri, section_index, segment_index, msg="none"):
    log_file = rec_file.split("_")[0] + ".log"
    uri_name = os.path.basename(current_uri)

    # Get current time in hh:mm:ss.milliseconds format
    now = datetime.now()
    ms = int(now.microsecond / 1000)
    timestamp = "%s.%03d" % (now.strftime("%H:%M:%S"), ms)

    # Ensure section_index and segment_index are integers, else assign -1
    try:
        section_index_int = int(section_index)
    except (ValueError, TypeError):
        section_index_int = -1
    try:
        segment_index_int = int(segment_index)
    except (ValueError, TypeError):
        segment_index_int = -1

    with open(log_file, 'a') as log_f:
        log_f.write("%s %s %03d/%03d: %s - %s\n" % (timestamp, ID, section_index_int, segment_index_int, uri_name, msg))
        log_f.flush()

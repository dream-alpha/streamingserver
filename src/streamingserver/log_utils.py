# coding=utf-8
#
# Copyright (C) 2018-2025 by dream-alpha
#
# In case of reuse of this source code please do not remove this copyright.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# For more information on the GNU General Public License see:
# <http://www.gnu.org/licenses/>.

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
    timestamp = "%s.%03d" % (now.strftime("%H:%M:%S"), ms)  # pylint: disable=consider-using-f-string

    try:
        section_index = "%03d" % int(section_index)  # pylint: disable=consider-using-f-string
    except (ValueError, TypeError):
        section_index = "---"
    try:
        segment_index = "%03d" % int(segment_index)  # pylint: disable=consider-using-f-string
    except (ValueError, TypeError):
        segment_index = "---"

    with open(log_file, 'a') as log_f:  # pylint: disable=unspecified-encoding
        log_f.write("%s %s %s/%s: %s - %s\n" % (timestamp, ID, section_index, segment_index, current_uri, msg))  # pylint: disable=consider-using-f-string
        log_f.flush()

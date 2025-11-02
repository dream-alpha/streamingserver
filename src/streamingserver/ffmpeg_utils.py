# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import subprocess
from debug import get_logger


logger = get_logger(__file__)


def open_ffmpeg_process(section_file):
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+discardcorrupt+genpts+igndts+ignidx+nofillin",
        "-f", "mpegts", "-i", "-",
        "-map", "0:v?", "-map", "0:a?",  # Optional mapping - don't fail if streams missing
        "-flush_packets", "1",   # Flush packets immediately
        "-c", "copy", section_file
    ]
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)  # pylint: disable=consider-using-with
    logger.debug("Started ffmpeg process for section: %s", section_file)
    return ffmpeg_proc


def close_ffmpeg_process(ffmpeg_proc):
    if ffmpeg_proc is not None:
        try:
            ffmpeg_proc.stdin.close()
            ffmpeg_proc.wait()
            logger.debug("Closed ffmpeg process.")
        except Exception as e:
            logger.error("Error closing ffmpeg process: %s", e)


def write_ffmpeg_segment(ffmpeg_proc, segment_data):
    if ffmpeg_proc is not None and ffmpeg_proc.stdin:
        try:
            ffmpeg_proc.stdin.write(segment_data)
            ffmpeg_proc.stdin.flush()
        except Exception as e:
            logger.error("Error writing segment to ffmpeg stdin: %s", e)


def terminate_ffmpeg_process(ffmpeg_proc):
    if ffmpeg_proc is not None:
        try:
            ffmpeg_proc.kill()
            ffmpeg_proc.wait(timeout=5)
            logger.debug("Terminated ffmpeg process.")
        except Exception as e:
            logger.error("Error terminating ffmpeg process: %s", e)

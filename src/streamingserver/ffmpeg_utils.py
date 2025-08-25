import os
import subprocess
from debug import get_logger


logger = get_logger(__file__)


def open_ffmpeg_process(section_file, section_index):
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+discardcorrupt+genpts+igndts+ignidx+nofillin",
        "-err_detect", "ignore_err",
        "-f", "mpegts", "-i", "-",
        "-c", "copy", section_file
    ]
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)  # pylint: disable=consider-using-with
    logger.info("Started ffmpeg for section %s: %s", section_index, section_file)
    return ffmpeg_proc


def close_ffmpeg_process(ffmpeg_proc, section_index):
    if ffmpeg_proc is not None:
        try:
            ffmpeg_proc.stdin.close()
            ffmpeg_proc.wait()
            logger.info("Closed previous ffmpeg process for section %s", section_index)
        except Exception as e:
            logger.error("Error closing ffmpeg process: %s", e)


def write_ffmpeg_segment(ffmpeg_proc, segment_data, rec_file, current_uri, segment_index):
    log_file = os.path.splitext(rec_file)[0] + '.log'
    uri_name = os.path.basename(current_uri)
    # pkt_file = os.path.splitext(rec_file)[0] + f"_{segment_index}.ts"

    if ffmpeg_proc is not None and ffmpeg_proc.stdin:
        try:
            ffmpeg_proc.stdin.write(segment_data)
            ffmpeg_proc.stdin.flush()
        except Exception as e:
            logger.error("Error writing segment to ffmpeg stdin: %s", e)

        with open(log_file, 'a', encoding="utf-8") as log_f:
            log_f.write(f"{segment_index}: {uri_name}\n")

def terminate_ffmpeg_process(ffmpeg_proc):
    if ffmpeg_proc is not None:
        try:
            ffmpeg_proc.kill()
            ffmpeg_proc.wait(timeout=5)
            logger.info("Terminated ffmpeg process.")
        except Exception as e:
            logger.error("Error terminating ffmpeg process: %s", e)

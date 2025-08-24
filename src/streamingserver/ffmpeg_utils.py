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
            try:
                ffmpeg_proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.communicate()
            logger.info("Closed previous ffmpeg process for section %s", section_index)
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
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
                ffmpeg_proc.communicate()
            logger.info("Terminated ffmpeg process.")
        except Exception as e:
            logger.error("Error terminating ffmpeg process: %s", e)

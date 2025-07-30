import sys
import logging
from Version import ID
from config import config


log_levels = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
format_string = (
    ID + ": " + "%(levelname)s: %(filename)s: %(funcName)s:%(lineno)d: %(message)s"
)


def get_logger(module_name=None, log_level=None):
    """
    Returns a logger for the given module name with the specified log level.
    Each logger gets its own handler and level.
    """
    if module_name is None:
        module_name = ID
    logger = logging.getLogger(module_name)
    logger.propagate = False
    # Remove all existing handlers to avoid duplicates
    while logger.handlers:
        logger.removeHandler(logger.handlers[0])
    formatter = logging.Formatter(format_string)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    # Determine log level
    if log_level:
        desired_log_level = log_level
    else:
        desired_log_level = config.plugins.streamingserver.debug_log_level.value
    level = log_levels.get(desired_log_level, logging.INFO)
    logger.setLevel(level)
    handler.setLevel(level)
    logger.addHandler(handler)
    return logger

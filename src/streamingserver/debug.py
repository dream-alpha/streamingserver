# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Logger Configuration for the Streaming Server Plugin

This module sets up a flexible logging system for the application. It allows for
log levels to be configured on a per-module basis through a `debug_config.txt`
file, with a fallback to a global setting from the main configuration.

The `get_logger` function is the main entry point for obtaining a configured
logger instance.
"""
import os
import sys
import logging
from version import ID
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
    Initializes and returns a logger with a specific configuration.

    This function creates a logger for a given module. It dynamically sets the
    log level based on the following priority:
    1. The `log_level` argument passed to the function.
    2. A module-specific level defined in `debug_config.txt`.
    3. A global default level from the main `config` object.

    Each logger is configured with a custom format and a stream handler that
    outputs to stdout. Handler propagation is disabled to prevent duplicate
    log messages.

    Args:
        module_name (str, optional): The name of the module for which to create
                                     the logger. If it's a file path, the basename
                                     is used. Defaults to the global application ID.
        log_level (str, optional): A specific log level to force for this logger
                                   (e.g., "DEBUG", "INFO"). Defaults to None.

    Returns:
        logging.Logger: A configured logger instance.
    """
    if module_name is None:
        module_name = ID
    # If module_name looks like a path, use basename for config matching
    if isinstance(module_name, str) and (module_name.endswith('.py') or os.sep in module_name):
        module_name = os.path.basename(module_name)
    logger = logging.getLogger(module_name)
    logger.propagate = False
    # Remove all existing handlers to avoid duplicates
    while logger.handlers:
        logger.removeHandler(logger.handlers[0])
    formatter = logging.Formatter(format_string)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Try to read log level from debug_config.txt
    file_log_level = None
    config_path = os.path.join(os.path.dirname(__file__), "debug_config.txt")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    mod, lvl = [x.strip() for x in line.split(":", 1)]
                    lvl = lvl.strip('"')
                    if mod == module_name:
                        file_log_level = lvl
                        break
    except Exception:
        logger.error("Could not read debug_config.txt at %s", config_path)

    # Determine log level
    if log_level:
        desired_log_level = log_level
    elif file_log_level:
        desired_log_level = file_log_level
    else:
        desired_log_level = config.plugins.streamingserver.debug_log_level.value
    # Ensure desired_log_level is a string
    if not isinstance(desired_log_level, str):
        desired_log_level = str(desired_log_level)

    level = log_levels.get(desired_log_level, logging.INFO)
    logger.setLevel(level)
    handler.setLevel(level)
    logger.addHandler(handler)
    return logger

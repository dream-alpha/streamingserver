# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Configuration Management for the Streaming Server

This module provides a flexible configuration management system that loads settings
from a text file (`settings.txt`). It parses key-value pairs, organizes them into
a hierarchical namespace, and automatically casts values to their appropriate
Python types (e.g., int, bool, list, str).

The configuration is accessed through a singleton `config` object.

Key features:
- Hierarchical configuration using dot notation (e.g., `config.plugins.streamingserver.port`).
- Automatic type casting for common Python literals.
- A `ValueWrapper` class to access the raw value via the `.value` attribute.
- A fallback mechanism that returns a special `_MissingConfigValue` object
  for undefined settings, preventing `AttributeError` exceptions.
- Automatic loading of a default `settings.txt` file if present.
"""

import ast
import os


DEFAULT_CONFIG_FILE = "/etc/enigma2/settings.txt"


class ValueWrapper:
    """
    A wrapper class for configuration values.

    This class holds the actual configuration value and provides methods to
    access it as different types (e.g., int, float, bool). The primary way
    to access the value is through the `.value` attribute.
    """
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"<ValueWrapper value={self.value!r}>"

    def __str__(self):
        return str(self.value)

    def __int__(self):
        return int(self.value)

    def __float__(self):
        return float(self.value)

    def __bool__(self):
        return bool(self.value)


class _MissingConfigValue:
    """
    A dummy object representing a missing configuration value.

    This class is used as a fallback for undefined settings. It always returns
    `None` for its `.value` attribute and returns itself for any other attribute
    access, allowing for safe, chainable lookups on non-existent paths.
    It evaluates to `False` in a boolean context.
    """
    value = None

    def __getattr__(self, key):
        return self

    def __bool__(self):
        return False

    def __repr__(self):
        return "None"


class ConfigNamespace:
    """
    A namespace for organizing hierarchical configuration settings.

    This class acts like a dictionary but allows attribute-style access
    (e.g., `namespace.key`). If a key is not found, it returns a
    `_MissingConfigValue` instance to prevent errors.
    """
    def __init__(self):
        self.__dict__ = {}

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value

    def __getattr__(self, key):
        if key in self.__dict__:
            return self.__dict__[key]
        return _MissingConfigValue()

    def __setattr__(self, key, value):
        if key == "_ConfigNamespace__dict__":
            super().__setattr__(key, value)
        else:
            self.__dict__[key] = value

    def __repr__(self):
        return repr(self.__dict__)


class _Config(ConfigNamespace):
    """
    The main configuration class that handles loading and parsing.

    This class inherits from `ConfigNamespace` and adds the functionality
    to load settings from a file. It is intended to be used as a singleton.
    """
    def __init__(self):
        super().__init__()
        self._loaded_file = None

    def load_file(self, filename):
        """
        Load and parse a configuration file.

        Args:
            filename (str): The path to the configuration file.

        Raises:
            FileNotFoundError: If the specified file does not exist.
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"Config file not found: {filename}")
        self._loaded_file = filename

        with open(filename, 'r', encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if '=' not in line:
                    print(f"Skipping line {lineno}: invalid format")
                    continue

                key_path, value = [s.strip() for s in line.split('=', 1)]
                if key_path.startswith("config."):
                    key_path = key_path[len("config."):]

                keys = key_path.split('.')
                current = self
                for key in keys[:-1]:
                    if not hasattr(current, key):
                        setattr(current, key, ConfigNamespace())
                    current = getattr(current, key)

                cast_value = self._auto_cast(value)
                setattr(current, keys[-1], ValueWrapper(cast_value))

    def reload(self):
        """Reload the config file if it was loaded before."""
        if self._loaded_file:
            self.load_file(self._loaded_file)

    @staticmethod
    def _auto_cast(value):
        """
        Automatically cast a string value to a Python literal.

        Tries to evaluate the string as a Python literal (e.g., number, boolean,
        list). If that fails, it checks for a comma-separated list, and finally
        returns the original string.

        Args:
            value (str): The string value to cast.

        Returns:
            The casted value.
        """
        value = value.strip()
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
        if ',' in value:
            return [_Config._auto_cast(v.strip()) for v in value.split(',')]
        return value


# Singleton instance of the configuration manager.
config = _Config()

# Automatically load the default `settings.txt` file if it exists.
if os.path.exists(DEFAULT_CONFIG_FILE):
    try:
        config.load_file(DEFAULT_CONFIG_FILE)
    except Exception as e:
        print(f"Warning: Failed to load config from default file: {e}")

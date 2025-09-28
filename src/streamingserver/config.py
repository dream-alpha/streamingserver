# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Configuration Management for the Streaming Server

This module provides a flexible configuration management system that loads settings
from a text file (`settings`). It parses key-value pairs, organizes them into
a hierarchical namespace, and automatically casts values to their appropriate
Python types (e.g., int, bool, list, str).

The configuration is accessed through a singleton `config` object.

Key features:
- Hierarchical configuration using dot notation (e.g., `config.plugins.streamingserver.port`).
- Automatic type casting for common Python literals.
- A `ValueWrapper` class to access the raw value via the `.value` attribute.
- A fallback mechanism that returns `None` for undefined settings.
- Automatic loading of a default `settings` file if present.
"""

import ast
import os


# Use the actual settings file
DEFAULT_CONFIG_FILE = "/etc/enigma2/settings"


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
    def __init__(self):
        # Note: We don't set value as an instance attribute to ensure __getattribute__ is used
        pass

    def __getattr__(self, _akey):
        # For any attribute access except 'value', return self to allow chaining
        return self

    def __bool__(self):
        return False

    def __repr__(self):
        return "None"

    def __getattribute__(self, name):
        # Special case for .value to ensure it always returns None
        if name == "value":
            return None
        # For all other attribute lookups, use the default behavior
        return object.__getattribute__(self, name)


class ConfigNamespace:
    """
    A namespace for organizing hierarchical configuration settings.

    This class acts like a dictionary but allows attribute-style access
    (e.g., `namespace.key`). If a key is not found, it returns a
    `_MissingConfigValue` instance to prevent errors.
    """
    def __init__(self):
        # Initialize with an empty dictionary
        self.__dict__ = {}

    def __getitem__(self, akey):
        return self.__dict__[akey]

    def __setitem__(self, akey, value):
        self.__dict__[akey] = value

    def __getattr__(self, _akey):
        # This is called only when an attribute is not found
        # through normal attribute lookup
        return _MissingConfigValue()

    def __setattr__(self, akey, value):
        # Always set attributes directly in the __dict__
        self.__dict__[akey] = value

    def __repr__(self):
        return repr(self.__dict__)


class _Config(ConfigNamespace):
    """
    The main configuration class that handles loading and parsing.

    This class inherits from `ConfigNamespace` and adds the functionality
    to load settings from a file. It is intended to be used as a singleton.
    """
    def __init__(self):
        super().__init__()  # Initialize with empty dict
        self._loaded_file = None

    def __getattr__(self, _akey):
        # Return _MissingConfigValue for missing attributes
        return _MissingConfigValue()

    def load_file(self, filename):
        """
        Load and parse a configuration file.

        Args:
            filename (str): The path to the configuration file.
        """
        if not os.path.exists(filename):
            print(f"Warning: Config file not found: {filename}")
            return

        print(f"Loading configuration from {filename}")
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

                # Always require and use the 'config.' prefix
                if not key_path.startswith("config."):
                    print(f"Skipping line {lineno}: key does not start with 'config.': {key_path}")
                    continue
                stripped_key_path = key_path[len("config."):]

                # Process the path generically for all key paths
                # Insert at root (self) for config.plugins... instead of self.config.plugins...
                keys = stripped_key_path.split('.')
                current = self

                # Create all necessary namespaces in the path
                for akey in keys[:-1]:
                    # Create namespace if it doesn't exist
                    if akey not in current.__dict__:
                        current.__dict__[akey] = ConfigNamespace()
                    # Move to the next level
                    current = current.__dict__[akey]

                # Set the value at the final level
                final_key = keys[-1]
                # Auto-cast the value to appropriate type
                cast_value = self._auto_cast(value)
                # Set directly in __dict__ to bypass __getattr__
                current.__dict__[final_key] = ValueWrapper(cast_value)

                # Insert at root (self) for config.plugins... instead of self.config.plugins...
                keys = stripped_key_path.split('.')
                current = self

                # Create all necessary namespaces in the path
                for akey in keys[:-1]:
                    # Check if current attribute exists and is not a ValueWrapper
                    if not hasattr(current, akey) or isinstance(getattr(current, akey), _MissingConfigValue):
                        # Create namespace if it doesn't exist or is a MissingConfigValue
                        setattr(current, akey, ConfigNamespace())
                    elif isinstance(getattr(current, akey), ValueWrapper):
                        # If it's a ValueWrapper, we cannot continue this branch
                        print(f"Warning: Cannot create namespace under ValueWrapper at '{akey}' in path '{key_path}'")
                        break

                    # Move to the next level in the hierarchy
                    current = getattr(current, akey)
                else:
                    # 'else' block runs if the for loop wasn't broken
                    # We've successfully navigated to the right location for the final key
                    cast_value = self._auto_cast(value)
                    # print("Loaded config:", key_path, "=", cast_value)

                    final_key = keys[-1]
                    # Check if the attribute already exists at this level
                    if hasattr(current, final_key):
                        existing = getattr(current, final_key)
                        if isinstance(existing, ConfigNamespace) and existing.__dict__:  # has children
                            print(f"Warning: Not overwriting namespace with children at '{key_path}'")
                            continue

                    # Set the value directly in the current object's __dict__
                    # to bypass __getattr__ and __setattr__
                    current.__dict__[final_key] = ValueWrapper(cast_value)

        print(f"Successfully loaded configuration from {filename}")

    def reload(self):
        """Reload the config file if it was loaded before."""
        if self._loaded_file:
            self.load_file(self._loaded_file)

    @staticmethod
    def _auto_cast(value):
        """
        Automatically cast a string value to a Python literal.

        Tries to evaluate the string as a Python literal (e.g., number, boolean,
        list). If that fails, it returns the original string.

        Args:
            value (str): The string value to cast.

        Returns:
            The casted value.
        """
        value = value.strip()

        # Try to evaluate as a Python literal (handles numbers, booleans, lists, etc.)
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            # If it's not a valid Python literal, return as string
            return value


# Singleton instance of the configuration manager.
config = _Config()

# Automatically load the default `settings` file if it exists.
if os.path.exists(DEFAULT_CONFIG_FILE):
    try:
        config.load_file(DEFAULT_CONFIG_FILE)
        print(f"Successfully loaded configuration from {DEFAULT_CONFIG_FILE}")

        # Let's print the actual dictionary structure to see what got loaded
        # print("DEBUG: Config structure after loading:")
        # for key in config.__dict__:
        #     if key != "_loaded_file" and not key.startswith("_"):
        #         print(f"  {key} = {config.__dict__[key]}")

    except Exception as e:
        print(f"Warning: Failed to load config from default file: {e}")
else:
    print(f"Warning: Config file not found at {DEFAULT_CONFIG_FILE}")
    print("No configuration loaded. Using empty configuration object.")

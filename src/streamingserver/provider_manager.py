# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from __future__ import annotations

import sys
import os
import importlib
import importlib.util
import json
import re
from typing import Any
from version import PLUGIN
from debug import get_logger

logger = get_logger(__file__)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class ProviderManager:
    """
    Dynamic provider loader for streamingserver providers
    """

    def __init__(self):
        self.providers: dict[str, Any] = {}
        self.active_provider: str | None = None
        self.providers_dir = os.path.join(os.path.dirname(__file__), 'providers')

        # Dynamically scan for available providers
        self.supported_providers = self._scan_available_providers()

    def _scan_available_providers(self) -> dict[str, dict[str, str]]:
        """
        Dynamically scan the providers directory for available providers
        Returns:
            Dict of provider configurations discovered from directory structure
        """
        discovered_providers = {}

        if not os.path.exists(self.providers_dir):
            logger.info("Providers directory not found: %s", self.providers_dir)
            return discovered_providers

        try:
            # Scan directory for provider folders
            for item in os.listdir(self.providers_dir):
                provider_path = os.path.join(self.providers_dir, item)

                # Skip files and hidden directories
                if not os.path.isdir(provider_path) or item.startswith('_'):
                    continue

                provider_id = item  # Keep original case for class name matching
                logger.info("Discovered provider directory: %s", provider_id)

                # Try to determine the provider configuration
                config = self._analyze_provider_structure(provider_id, provider_path)
                if config:
                    discovered_providers[provider_id] = config
                    logger.info("Provider %s configured successfully", provider_id)
                else:
                    logger.info("Could not configure provider %s", provider_id)

        except Exception as e:
            logger.info("Error scanning providers directory: %s", e)

        logger.info("Discovered %s providers: %s", len(discovered_providers), list(discovered_providers.keys()))
        return discovered_providers

    def _analyze_provider_structure(self, provider_id: str, provider_path: str) -> dict[str, str] | None:
        """
        Analyze provider directory structure to determine configuration
        Returns:
            Provider configuration dict or None if invalid structure
        """
        try:
            # Check for required files
            init_file = os.path.join(provider_path, '__init__.py')
            config_file = os.path.join(provider_path, 'config.json')

            if not os.path.exists(init_file):
                logger.info("Missing __init__.py in %s", provider_id)
                return None

            # Load configuration from config.json if available
            provider_config = self._load_provider_config(config_file, provider_id)

            # Parse __init__.py for exported classes (most accurate method)
            try:
                with open(init_file, 'r', encoding='utf-8') as f:
                    init_content = f.read()

                # Look for exported classes in __all__ list

                # Find __all__ list to get exported classes
                all_match = re.search(r'__all__\s*=\s*\[(.*?)\]', init_content, re.DOTALL)
                exported_classes = []

                if all_match:
                    all_content = all_match.group(1)
                    # Extract class names from __all__ list
                    class_matches = re.findall(r'[\'"](\w+)[\'"]', all_content)
                    exported_classes = class_matches

                if exported_classes:
                    # Ultra-generic: all providers use generic 'Provider' class
                    expected_class_name = "Provider"

                    # Check if generic Provider class is exported
                    if expected_class_name in exported_classes:
                        return {
                            'module_path': f'providers.{provider_id}',
                            'class_name': expected_class_name,
                            'display_name': provider_config['name'],
                            'description': provider_config['description'],
                            'thumbnail': provider_config['thumbnail'],
                            'provider_id': provider_config['provider_id']
                        }

                    # Report error if generic Provider class not found
                    logger.info("Generic 'Provider' class not found in %s", provider_id)
                    logger.info("Available classes: %s", exported_classes)
                    return None

            except Exception as e:
                logger.info("Could not read __init__.py for %s: %s", provider_id, e)

            logger.info("Could not determine provider structure for %s", provider_id)
            return None

        except Exception as e:
            logger.info("Error analyzing provider %s: %s", provider_id, e)
            return None

    def _load_provider_config(self, config_file: str, provider_id: str) -> dict[str, str]:
        """
        Load provider configuration from config.json file
        Returns:
            Dict with provider metadata (id, name, thumbnail, description)
        """
        default_config = {
            'provider_id': provider_id,
            'name': provider_id.capitalize(),
            'thumbnail': f'{provider_id}.png',
            'description': ''
        }

        if not os.path.exists(config_file):
            logger.info("No config.json found, using defaults for %s", provider_id)
            return default_config

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            # Validate required fields
            required_fields = ['name', 'thumbnail', 'description']
            for field in required_fields:
                if field not in config_data:
                    logger.info("Missing '%s' in config.json for %s", field, provider_id)
                    config_data[field] = default_config[field]
            config_data['provider_id'] = provider_id

            logger.info("Loaded config.json for %s", provider_id)
            return config_data

        except Exception as e:
            logger.info("Error reading config.json for %s: %s", provider_id, e)
            return default_config

    def get_available_providers(self) -> dict[str, dict[str, str]]:
        """
        Get list of available providers with their metadata
        Returns:
            Dict of provider configurations
        """
        available = {}

        for provider_id, config in self.supported_providers.items():
            if self._check_provider_exists(provider_id):
                available[provider_id] = {
                    'name': provider_id,
                    'provider_id': config.get('provider_id', provider_id),
                    'description': config['description'],
                    'thumbnail': config.get('thumbnail', f'{provider_id}_logo.png'),
                }

        return available

    def _check_provider_exists(self, provider_id: str) -> bool:
        """Check if provider directory and files exist"""
        provider_dir = os.path.join(self.providers_dir, provider_id)
        return os.path.isdir(provider_dir)

    def load_provider(self, provider_id: str, data_dir: str) -> Any | None:
        """
        Dynamically load a provider by name from the providers directory

        Args:
            provider_id (str): Name of provider (xhamster, xnxx, xvideos)
            data_dir (str): data directory

        Returns:
            Provider instance or None if loading failed
        """

        try:
            provider_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), PLUGIN, 'providers', provider_id)
            logger.info("Importing provider module: %s from providers dir: %s", provider_id, provider_dir)
            if not os.path.exists(provider_dir):
                logger.error("Could not find %s.py in provider directory", provider_id)
                return None
            config = self.supported_providers[provider_id]
            logger.info("Loading provider: %s", config['display_name'])

            # Load provider class using ultra-simplified naming
            module = importlib.import_module(config['module_path'])
            provider_class = getattr(module, config['class_name'], None)

            if provider_class:
                logger.info("Found unified class: %s", config['class_name'])
                instance = provider_class(provider_id, data_dir)

                # Validate the instance has required methods
                required_methods = ['get_categories', 'get_media_items']
                if all(hasattr(instance, method) for method in required_methods):
                    logger.info("Provider %s loaded successfully", provider_id)
                    return instance

                logger.info("Provider missing required methods")
            else:
                logger.info("Class %s not found in %s", config['class_name'], config['module_path'])

            return None

        except Exception as e:
            logger.info("Error loading provider %s: %s", provider_id, e)
            return None

    def get_provider(self, provider_id: str, data_dir: str) -> Any | None:
        """
        Get a provider instance by name. Loads if not already loaded.

        Args:
            provider_id (str): Name of provider to get
            data_dir (str): data directory

        Returns:
            Provider instance or None if not found
        """
        data_dir = data_dir / provider_id

        if provider_id != self.active_provider:
            logger.info("Unload active provider: %s", self.active_provider)
            self.unload_provider(self.active_provider)

        if provider_id not in self.providers:
            instance = self.load_provider(provider_id, data_dir)
            if instance:
                self.providers[provider_id] = instance
                self.active_provider = provider_id
                if hasattr(instance, 'update_channel_data'):
                    instance.update_channel_data()

        provider_instance = self.providers.get(provider_id)

        # Create data directory if it doesn't exist
        data_dir.mkdir(parents=True, exist_ok=True)

        return provider_instance

    def unload_provider(self, provider_id: str) -> bool:
        """Unload a provider from memory"""
        if provider_id in self.providers:
            del self.providers[provider_id]
            if self.active_provider == provider_id:
                self.active_provider = None
            logger.info("Provider %s unloaded", provider_id)
            return True
        return False

    def get_providers(self) -> list[dict[str, str]]:
        """
        Get all provider configurations from config.json files

        Returns:
            List of provider configuration dictionaries with metadata
        """
        providers_list = []

        if not os.path.exists(self.providers_dir):
            logger.info("Providers directory not found: %s", self.providers_dir)
            return providers_list

        try:
            # Scan directory for provider folders
            for item in os.listdir(self.providers_dir):
                provider_path = os.path.join(self.providers_dir, item)

                # Skip files and hidden directories
                if not os.path.isdir(provider_path) or item.startswith('_'):
                    continue

                provider_id = item  # Keep original case for ultra-simplified system
                config_file = os.path.join(provider_path, 'config.json')
                provider_config = self._load_provider_config(config_file, provider_id)
                providers_list.append(provider_config)

        except Exception as e:
            logger.info("Error scanning providers for configs: %s", e)

        return providers_list

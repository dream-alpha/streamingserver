# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from __future__ import annotations

import sys
import os
import importlib
import json
from typing import Any

# Add current directory to path for standalone execution
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from debug import get_logger
except ImportError:
    try:
        from .debug import get_logger
    except ImportError:
        # Fallback logging if debug module not available
        import logging

        def get_logger(name):
            return logging.getLogger(name.replace('.py', ''))

logger = get_logger(__file__)


class ProviderManager:
    """Provider loader for streamingserver providers"""

    def __init__(self):
        self.providers: dict[str, Any] = {}
        self.active_provider: str | None = None
        self.providers_dir = os.path.join(os.path.dirname(__file__), 'providers')

    def get_providers(self) -> list[dict[str, str]]:
        """Get all provider configurations from config.json files"""
        providers_list = []

        if not os.path.exists(self.providers_dir):
            logger.error("Providers directory not found: %s", self.providers_dir)
            return providers_list

        try:
            for provider_id in os.listdir(self.providers_dir):
                provider_path = os.path.join(self.providers_dir, provider_id)
                # Skip files and hidden directories
                if not os.path.isdir(provider_path) or provider_id.startswith('_'):
                    continue

                config_file = os.path.join(provider_path, 'config.json')
                provider_config = self._load_provider_config(config_file, provider_id)
                provider_config["provider_id"] = provider_id
                providers_list.append(provider_config)

        except Exception as e:
            logger.error("Error scanning providers: %s", e)

        # Sort providers alphabetically by name (case-insensitive)
        providers_list.sort(key=lambda x: x.get('name', x.get('title', x.get('provider_id', ''))).lower())

        return providers_list

    def load_provider(self, provider_id: str, data_dir: str) -> Any | None:
        """Load a provider by importing its module and creating an instance"""
        try:
            # Import the provider module
            module_path = f'providers.{provider_id}'
            module = importlib.import_module(module_path)

            # Get the Provider class (all providers use 'Provider')
            provider_class = getattr(module, 'Provider', None)
            if not provider_class:
                logger.error("Provider class not found in %s", provider_id)
                return None

            # Create instance
            instance = provider_class(provider_id, data_dir)
            logger.info("Provider %s loaded successfully", provider_id)
            return instance

        except Exception as e:
            logger.error("Error loading provider %s: %s", provider_id, e)
            return None

    def _load_provider_config(self, config_file: str, provider_id: str) -> dict[str, str]:
        """Load provider configuration from config.json file"""
        default_config = {
            'provider_id': provider_id,
            'name': provider_id.capitalize(),
            'thumbnail': f'{provider_id}.png',
            'description': f'{provider_id.capitalize()} provider',
            'quality': 'best'  # Default quality preference for provider
        }

        if not os.path.exists(config_file):
            return default_config

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            # Merge with defaults
            config_data['provider_id'] = provider_id
            for key, default_value in default_config.items():
                if key not in config_data:
                    config_data[key] = default_value

            return config_data

        except Exception as e:
            logger.error("Error reading config.json for %s: %s", provider_id, e)
            return default_config

    def get_provider(self, provider_id: str, data_dir: str) -> Any | None:
        """Get a provider instance by name. Loads if not already loaded."""
        # Unload previous provider if different
        if provider_id != self.active_provider:
            self.unload_provider(self.active_provider)

        # Load provider if not already loaded
        if provider_id not in self.providers:
            instance = self.load_provider(provider_id, data_dir)
            if instance:
                self.providers[provider_id] = instance
                self.active_provider = provider_id

        if hasattr(self.active_provider, "update_channel_data"):
            self.active_provider.update_channel_data()
        return self.providers.get(provider_id)

    def unload_provider(self, provider_id: str) -> bool:
        """Unload a provider from memory"""
        if hasattr(self.active_provider, "stop_updates"):
            self.active_provider.stop_updates()
        if provider_id and provider_id in self.providers:
            del self.providers[provider_id]
            if self.active_provider == provider_id:
                self.active_provider = None
            logger.info("Provider %s unloaded", provider_id)
            return True
        return False

# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from __future__ import annotations

import os
import importlib
import json
from typing import Any
from debug import get_logger

logger = get_logger(__file__)


class ProviderManager:
    """Provider loader for streamingserver providers"""

    def __init__(self):
        self.active_provider: Any | None = None
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

                config_data = None
                config_file = os.path.join(provider_path, 'config.json')
                with open(config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                if config_data:
                    # Add provider_id (directory name) to the config
                    config_data['provider_id'] = provider_id
                    providers_list.append(config_data)

        except Exception as e:
            logger.error("Error scanning providers: %s", e)

        # Sort providers alphabetically by name (case-insensitive)
        providers_list.sort(key=lambda x: x.get('name', x.get('title', x.get('provider_id', ''))).lower())
        return providers_list

    def get_provider(self, provider_id: str, args: dict) -> Any | None:
        """Get a provider instance by name. Creates a fresh instance each time."""
        if self.active_provider and hasattr(self.active_provider, "stop_updates"):
            self.active_provider.stop_updates()
        # Always create a fresh instance
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
            self.active_provider = provider_class(args)
            logger.info("Provider %s loaded successfully", provider_id)
            return self.active_provider

        except Exception as e:
            logger.error("Error loading provider %s: %s", provider_id, e)
            return None

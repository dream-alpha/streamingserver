# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import sys
import os
import importlib.util
from version import PLUGIN
from debug import get_logger

logger = get_logger(__file__)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class Provider():
    def __init__(self):
        self.providers = {}
        self.active_provider = None

    def load_provider(self, provider_name, data_dir):
        """
        Dynamically load a provider by name from the providers directory
        """
        try:
            providers_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), PLUGIN, 'providers')
            logger.info("Importing provider module: %s from providers dir: %s", provider_name, providers_dir)
            provider_path = os.path.join(providers_dir, provider_name + '.py')
            if not os.path.exists(provider_path):
                logger.error("Could not find %s.py in providers directory", provider_name)
                return None
            spec = importlib.util.spec_from_file_location(provider_name, provider_path)
            if spec is None:
                logger.error("Could not create spec for %s", provider_name)
                return None
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                logger.error("Error loading module %s: %s", provider_name, str(e))
                return None
            provider_class = getattr(module, provider_name, None)
            if provider_class:
                logger.info("Successfully loaded %s provider", provider_name)
                if self.active_provider:
                    self.providers[self.active_provider].stop_updates()
                self.active_provider = provider_name
                new_provider_instance = provider_class(data_dir)
                new_provider_instance.update_channel_data()
                return new_provider_instance
            logger.error("%s class not found in %s module", provider_name, provider_name)
            return None
        except ImportError as e:
            logger.error("Error importing %s: %s", provider_name, str(e))
        except Exception as e:
            logger.error("Error importing %s using importlib: %s", provider_name, str(e))
        return None

    def get_provider(self, provider_name, data_dir):
        """
        Get a provider instance by name. Loads the provider if not already loaded.
        Args:
            provider_name (str): Name of the provider to get
            data_dir (str): Directory of the provider category, channel, and epg data
        Returns:
            Provider instance or None if not found
        """
        if provider_name not in self.providers:
            instance = self.load_provider(provider_name, data_dir)
            if instance:
                self.providers[provider_name] = instance
        return self.providers.get(provider_name)

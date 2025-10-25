# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from __future__ import annotations

import sys
import os
import importlib
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


class ResolverManager:
    """Resolver loader for streamingserver resolvers"""

    def __init__(self):
        self.resolvers: dict[str, Any] = {}
        self.active_resolver: str | None = None

    def load_resolver(self, provider_id: str) -> Any | None:
        """Load a resolver by importing its module and creating an instance"""
        try:
            # Import the resolver module
            module_path = f'providers.{provider_id}.resolver'
            module = importlib.import_module(module_path)

            # Get the resolver class (naming convention: Resolver)
            resolver_class_name = 'Resolver'
            resolver_class = getattr(module, resolver_class_name, None)
            if not resolver_class:
                logger.error("Resolver class %s not found in %s", resolver_class_name, provider_id)
                return None

            # Create instance
            instance = resolver_class()
            logger.info("Resolver %s loaded successfully", provider_id)
            return instance

        except Exception as e:
            logger.error("Error loading resolver %s: %s", provider_id, e)
            return None

    def get_resolver(self, provider_id: str) -> Any | None:
        """Get a resolver instance by provider name. Loads if not already loaded."""
        # Unload previous resolver if different
        if provider_id != self.active_resolver:
            self.unload_resolver(self.active_resolver)

        # Load resolver if not already loaded
        if provider_id not in self.resolvers:
            instance = self.load_resolver(provider_id)
            if instance:
                self.resolvers[provider_id] = instance
                self.active_resolver = provider_id

        return self.resolvers.get(provider_id)

    def unload_resolver(self, provider_id: str) -> bool:
        """Unload a resolver from memory"""
        if provider_id and provider_id in self.resolvers:
            del self.resolvers[provider_id]
            if self.active_resolver == provider_id:
                self.active_resolver = None
            logger.info("Resolver %s unloaded", provider_id)
            return True
        return False

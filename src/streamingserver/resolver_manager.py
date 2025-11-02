# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

from __future__ import annotations

import importlib
from typing import Any
from debug import get_logger

logger = get_logger(__file__)


class ResolverManager:
    """Resolver loader for streamingserver resolvers"""

    def __init__(self):
        self.active_resolver: Any | None = None

    def get_resolver(self, provider_id: str, args: dict) -> Any | None:
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
            self.active_resolver = resolver_class(args)
            logger.info("Resolver %s loaded successfully", provider_id)
            return self.active_resolver

        except Exception as e:
            logger.error("Error loading resolver %s: %s", provider_id, e)
            return None

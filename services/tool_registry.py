"""
Tool Registry - Backward Compatibility Shim
Version: 2.1

This file maintains backward compatibility for existing imports.
All functionality has been refactored into services/registry/ module.

Import path unchanged: from services.tool_registry import ToolRegistry
"""

# Re-export everything from the refactored module
from services.registry import (
    ToolRegistry,
    ToolStore,
    CacheManager,
    SwaggerParser,
    EmbeddingEngine,
    SearchEngine
)

__all__ = [
    'ToolRegistry',
    'ToolStore',
    'CacheManager',
    'SwaggerParser',
    'EmbeddingEngine',
    'SearchEngine'
]

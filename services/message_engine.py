"""
Message Engine - Backward Compatibility Shim
Version: 14.0

This file maintains backward compatibility for existing imports.
All functionality has been refactored into services/engine/ module.

Import path unchanged: from services.message_engine import MessageEngine
"""

# Re-export from refactored module
from services.engine import MessageEngine

__all__ = ['MessageEngine']

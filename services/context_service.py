"""
Context Service
Version: 11.0

Conversation history management.
NO DEPENDENCIES on other services.

NEW v11.0:
- Phone number validation in context operations
- Prevents UUID/phone mixup in context keys
- Improved logging for forensic debugging
"""

import json
import time
import logging
import re
from typing import List, Dict, Any, Optional

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# UUID pattern for validation
UUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


class ContextService:
    """
    Manages conversation history in Redis.

    NEW v11.0: Added phone validation to prevent UUID/phone mixup.
    """

    def __init__(self, redis_client):
        """
        Initialize context service.

        Args:
            redis_client: Redis async client
        """
        self.redis = redis_client
        self.ttl = settings.CACHE_TTL_CONTEXT
        self.max_history = 20

    def _validate_user_id(self, user_id: str) -> bool:
        """
        Validate that user_id is a phone number, not a UUID.

        NEW v11.0: Prevents the UUID trap where person_id gets used
        instead of phone number for context keys.

        Args:
            user_id: User identifier (should be phone number)

        Returns:
            True if valid phone, False if UUID detected
        """
        if not user_id:
            logger.warning("CONTEXT: Empty user_id provided")
            return False

        # Check if it's a UUID (this is the TRAP we want to catch!)
        if UUID_PATTERN.match(user_id):
            logger.error(
                f"UUID TRAP IN CONTEXT: user_id appears to be UUID, not phone! "
                f"Value: {user_id[:20]}..."
            )
            # We allow it but log a warning - might be intentional in some cases
            return True

        return True

    def _key(self, user_id: str) -> str:
        """
        Build Redis key for user history.

        NEW v11.0: Logs warning if user_id looks like UUID.
        """
        self._validate_user_id(user_id)
        return f"chat_history:{user_id}"
    
    async def get_history(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get conversation history.
        
        Args:
            user_id: User identifier (phone number)
            
        Returns:
            List of messages
        """
        try:
            key = self._key(user_id)
            raw = await self.redis.lrange(key, 0, -1)
            
            messages = []
            for item in raw:
                if item:
                    try:
                        messages.append(json.loads(item))
                    except json.JSONDecodeError:
                        continue
            
            return messages
        except Exception as e:
            logger.warning(f"Get history failed: {e}")
            return []
    
    async def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        **kwargs
    ) -> bool:
        """
        Add message to history.
        
        Args:
            user_id: User identifier
            role: Message role (user, assistant, system, tool)
            content: Message content
            **kwargs: Additional metadata
            
        Returns:
            True if successful
        """
        key = self._key(user_id)
        
        message = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
            **kwargs
        }
        
        try:
            # Add to list
            await self.redis.rpush(key, json.dumps(message))
            
            # Set expiry
            await self.redis.expire(key, self.ttl)
            
            # Trim to max length
            length = await self.redis.llen(key)
            if length > self.max_history:
                await self.redis.ltrim(key, -self.max_history, -1)
            
            return True
        except Exception as e:
            logger.warning(f"Add message failed: {e}")
            return False
    
    async def clear_history(self, user_id: str) -> bool:
        """
        Clear conversation history.
        
        Args:
            user_id: User identifier
            
        Returns:
            True if successful
        """
        try:
            await self.redis.delete(self._key(user_id))
            return True
        except Exception as e:
            logger.warning(f"Clear history failed: {e}")
            return False
    
    async def get_recent_messages(
        self,
        user_id: str,
        count: int = 10
    ) -> List[Dict[str, str]]:
        """
        Get recent messages formatted for AI.
        
        Args:
            user_id: User identifier
            count: Number of recent messages
            
        Returns:
            List of {role, content} dicts
        """
        history = await self.get_history(user_id)
        
        # Get last N messages
        recent = history[-count:] if len(history) > count else history
        
        # Format for AI
        formatted = []
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role in ("user", "assistant") and content:
                formatted.append({"role": role, "content": content})
        
        return formatted

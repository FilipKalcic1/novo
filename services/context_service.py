"""
Context Service
Version: 10.0

Conversation history management.
NO DEPENDENCIES on other services.
"""

import json
import time
import logging
from typing import List, Dict, Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ContextService:
    """Manages conversation history in Redis."""
    
    def __init__(self, redis_client):
        """
        Initialize context service.
        
        Args:
            redis_client: Redis async client
        """
        self.redis = redis_client
        self.ttl = settings.CACHE_TTL_CONTEXT
        self.max_history = 20
    
    def _key(self, user_id: str) -> str:
        """Build Redis key for user history."""
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

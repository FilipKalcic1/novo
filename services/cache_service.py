"""
Cache Service
Version: 10.0

Redis caching layer.
NO DEPENDENCIES on other services.
"""

import json
import logging
from typing import Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class CacheService:
    """Redis cache wrapper."""
    
    def __init__(self, redis_client):
        """
        Initialize cache service.
        
        Args:
            redis_client: Redis async client
        """
        self.redis = redis_client
    
    async def get(self, key: str) -> Optional[str]:
        """Get value from cache."""
        try:
            return await self.redis.get(key)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            return None
    
    async def get_json(self, key: str) -> Optional[Any]:
        """Get JSON value from cache."""
        try:
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning(f"Cache get_json failed: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Set value with TTL.
        
        Args:
            key: Cache key
            value: Value to store (will be JSON encoded if not string)
            ttl: Time to live in seconds
            
        Returns:
            True if successful
        """
        try:
            if not isinstance(value, (str, bytes)):
                value = json.dumps(value)
            await self.redis.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Cache delete failed: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return await self.redis.exists(key) > 0
        except Exception:
            return False
    
    async def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Awaitable[Any]],
        ttl: int = 300
    ) -> Any:
        """
        Get from cache or compute value.
        
        Args:
            key: Cache key
            compute_fn: Async function to compute value if not cached
            ttl: Time to live
            
        Returns:
            Cached or computed value
        """
        # Try cache first
        cached = await self.get_json(key)
        if cached is not None:
            return cached
        
        # Compute value
        result = await compute_fn()
        
        # Cache result
        if result is not None:
            await self.set(key, result, ttl)
        
        return result
    
    async def increment(self, key: str, ttl: int = None) -> int:
        """
        Increment counter.
        
        Args:
            key: Counter key
            ttl: Optional TTL for first increment
            
        Returns:
            New value
        """
        try:
            value = await self.redis.incr(key)
            if ttl and value == 1:
                await self.redis.expire(key, ttl)
            return value
        except Exception as e:
            logger.warning(f"Cache increment failed: {e}")
            return 0

"""
Token Manager
Version: 10.0

OAuth2 token management.
DEPENDS ON: config.py only
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TokenManager:
    """
    Thread-safe OAuth2 token manager.
    
    Features:
    - Automatic refresh before expiry
    - Lock to prevent concurrent refreshes
    - Redis caching for distributed systems
    """
    
    def __init__(self, redis_client=None):
        """
        Initialize token manager.
        
        Args:
            redis_client: Optional Redis client for caching
        """
        self._token: Optional[str] = None
        self._expires_at: datetime = datetime.min
        self._refresh_lock = asyncio.Lock()
        self._redis = redis_client
        
        # Configuration
        self.auth_url = settings.MOBILITY_AUTH_URL
        self.client_id = settings.MOBILITY_CLIENT_ID
        self.client_secret = settings.MOBILITY_CLIENT_SECRET
        self.scope = settings.MOBILITY_SCOPE
        
        self._cache_key = "mobility:access_token"
        
        logger.info(f"TokenManager initialized: {self.auth_url}")
    
    async def get_token(self) -> str:
        """
        Get valid access token.
        
        Returns:
            Valid access token
            
        Raises:
            Exception if unable to obtain token
        """
        # Check in-memory cache (with 60s buffer)
        buffer = timedelta(seconds=60)
        if self._token and datetime.utcnow() < self._expires_at - buffer:
            return self._token
        
        # Try Redis cache
        if self._redis:
            try:
                cached = await self._redis.get(self._cache_key)
                if cached:
                    self._token = cached
                    self._expires_at = datetime.utcnow() + timedelta(minutes=5)
                    logger.debug("Token loaded from Redis")
                    return self._token
            except Exception as e:
                logger.warning(f"Redis cache read failed: {e}")
        
        # Refresh token (with lock)
        async with self._refresh_lock:
            # Double-check after lock
            if self._token and datetime.utcnow() < self._expires_at - buffer:
                return self._token
            
            return await self._fetch_new_token()
    
    async def _fetch_new_token(self) -> str:
        """Fetch new token from auth server."""
        logger.info("Fetching new OAuth2 token...")
        
        # IMPORTANT: Form-encoded, NOT JSON
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "audience": "none"
        }

        # Add scope if configured
        if self.scope:
            payload["scope"] = self.scope
            logger.debug(f"Requesting token with scope: {self.scope}")
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.auth_url,
                    data=payload,
                    headers=headers
                )
                
                if response.status_code != 200:
                    error_text = response.text[:500]
                    logger.error(f"Token fetch failed: {response.status_code} - {error_text}")
                    raise Exception(f"Auth failed ({response.status_code}): {error_text}")
                
                data = response.json()
                
                self._token = data["access_token"]
                expires_in = int(data.get("expires_in", 3600))
                self._expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
                
                logger.info(f"Token acquired, expires in {expires_in}s")
                
                # Cache in Redis
                if self._redis:
                    try:
                        cache_ttl = max(expires_in - 120, 60)
                        await self._redis.setex(self._cache_key, cache_ttl, self._token)
                        logger.debug(f"Token cached in Redis, TTL={cache_ttl}")
                    except Exception as e:
                        logger.warning(f"Redis cache write failed: {e}")
                
                return self._token
                
        except httpx.TimeoutException:
            logger.error("Token fetch timeout")
            raise Exception("Authentication timeout")
        except httpx.RequestError as e:
            logger.error(f"Token fetch network error: {e}")
            raise Exception(f"Authentication network error: {e}")
    
    def invalidate(self) -> None:
        """Invalidate current token (call on 401)."""
        logger.info("Token invalidated")
        self._token = None
        self._expires_at = datetime.min
        
        # Clear Redis cache
        if self._redis:
            asyncio.create_task(self._clear_redis_cache())
    
    async def _clear_redis_cache(self) -> None:
        """Clear Redis cache."""
        try:
            await self._redis.delete(self._cache_key)
        except Exception:
            pass
    
    @property
    def is_valid(self) -> bool:
        """Check if current token is valid."""
        if not self._token:
            return False
        return datetime.utcnow() < self._expires_at - timedelta(seconds=60)

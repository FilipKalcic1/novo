"""
Security Module
Version: 10.0

Webhook verification and rate limiting.
DEPENDS ON: config.py only
"""

import hashlib
import hmac
import time
import logging
from typing import Optional

from fastapi import Request, HTTPException

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def verify_infobip_signature(
    payload: bytes,
    signature: str,
    secret: Optional[str] = None
) -> bool:
    """
    Verify Infobip webhook signature.
    
    Args:
        payload: Raw request body
        signature: X-Infobip-Signature header
        secret: Webhook secret
        
    Returns:
        True if valid
    """
    if not signature:
        return False
    
    secret = secret or settings.INFOBIP_SECRET_KEY
    if not secret:
        # Allow in development without secret
        logger.warning("No Infobip secret configured")
        return True
    
    # TEMPORARY DEBUGGING START (REMOVE AFTER USE)
    if settings.DEBUG: # Only log this sensitive info in debug mode
        logger.debug(f"DEBUG: Using INFOBIP_SECRET_KEY for HMAC: '{secret}'")
    # TEMPORARY DEBUGGING END
    
    try:
        expected = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature.lower(), expected.lower())
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


async def verify_webhook(request: Request) -> bool:
    """
    FastAPI dependency to verify webhooks.
    
    Raises HTTPException if invalid.
    """
    if settings.DEBUG:
        return True
    
    signature = request.headers.get("X-Infobip-Signature", "")
    body = await request.body()
    
    if not verify_infobip_signature(body, signature):
        logger.warning(f"Invalid signature from {request.client.host if request.client else 'unknown'}")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    return True


def sanitize_phone(phone: str) -> str:
    """
    Sanitize phone number.
    
    Returns normalized format with +.
    """
    if not phone:
        return ""
    
    has_plus = phone.startswith("+")
    cleaned = "".join(c for c in phone if c.isdigit())
    
    if has_plus:
        return f"+{cleaned}"
    elif cleaned.startswith("00"):
        return f"+{cleaned[2:]}"
    elif cleaned.startswith("385"):
        return f"+{cleaned}"
    else:
        return f"+385{cleaned}"


def mask_phone(phone: str) -> str:
    """Mask phone for logging."""
    if not phone or len(phone) < 8:
        return "***"
    return f"{phone[:4]}...{phone[-4:]}"


class RateLimiter:
    """Simple in-memory rate limiter."""
    
    def __init__(self, limit: int = None, window: int = None):
        self.limit = limit or settings.RATE_LIMIT_PER_MINUTE
        self.window = window or settings.RATE_LIMIT_WINDOW
        self._requests: dict = {}
    
    def is_allowed(self, identifier: str) -> bool:
        """Check if request allowed."""
        now = time.time()
        window_start = now - self.window
        
        # Clean old entries
        if identifier in self._requests:
            self._requests[identifier] = [
                t for t in self._requests[identifier]
                if t > window_start
            ]
        else:
            self._requests[identifier] = []
        
        # Check limit
        if len(self._requests[identifier]) >= self.limit:
            return False
        
        # Add request
        self._requests[identifier].append(now)
        return True
    
    def get_remaining(self, identifier: str) -> int:
        """Get remaining requests."""
        now = time.time()
        window_start = now - self.window
        
        if identifier not in self._requests:
            return self.limit
        
        valid = [t for t in self._requests[identifier] if t > window_start]
        return max(0, self.limit - len(valid))

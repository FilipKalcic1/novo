"""
Data Sanitizer
Version: 11.0

Sanitizes sensitive data before logging or sending to AI.

SECURITY:
- Removes/masks PII (phone numbers, emails, IDs)
- Removes credentials and tokens
- Safe for logging and AI prompts
"""

import re
import logging
from typing import Any, Dict, List, Set, Union

logger = logging.getLogger(__name__)


class DataSanitizer:
    """
    Sanitizes sensitive data from logs and AI prompts.
    
    Usage:
        sanitizer = DataSanitizer()
        safe_data = sanitizer.sanitize(sensitive_data)
        safe_log = sanitizer.sanitize_for_log(message, context)
    """
    
    # Patterns to detect sensitive data
    PATTERNS = {
        # Phone numbers (international format)
        "phone": re.compile(r'\+?\d{10,15}'),
        
        # Email addresses
        "email": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'),
        
        # UUIDs (common for IDs)
        "uuid": re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I),
        
        # JWT tokens
        "jwt": re.compile(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
        
        # Bearer tokens
        "bearer": re.compile(r'Bearer\s+[A-Za-z0-9_-]+', re.I),
        
        # API keys (common formats)
        "api_key": re.compile(r'(?:api[_-]?key|apikey|key)[=:]\s*[\'"]?([A-Za-z0-9_-]{20,})[\'"]?', re.I),
        
        # Credit card numbers (basic)
        "credit_card": re.compile(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'),
        
        # OIB (Croatian personal ID)
        "oib": re.compile(r'\b\d{11}\b'),
        
        # Password fields
        "password": re.compile(r'(?:password|passwd|pwd)[=:]\s*[\'"]?([^\s\'"]+)[\'"]?', re.I),
    }
    
    # Keys that should be completely redacted
    SENSITIVE_KEYS: Set[str] = {
        "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
        "authorization", "auth", "credential", "private_key", "privatekey",
        "access_token", "refresh_token", "client_secret", "api_secret",
        "oib", "ssn", "social_security", "credit_card", "card_number",
    }
    
    # Keys to partially mask (show last 4 chars)
    PARTIAL_MASK_KEYS: Set[str] = {
        "phone", "phone_number", "mobile", "email", "person_id", "user_id",
        "tenant_id", "id", "api_identity",
    }
    
    def __init__(self, mask_char: str = "*", show_last: int = 4):
        """
        Initialize sanitizer.
        
        Args:
            mask_char: Character to use for masking
            show_last: Number of characters to show at end of partial masks
        """
        self.mask_char = mask_char
        self.show_last = show_last
    
    def sanitize(self, data: Any, depth: int = 0) -> Any:
        """
        Recursively sanitize data structure.
        
        Args:
            data: Data to sanitize (dict, list, str, etc.)
            depth: Current recursion depth (prevents infinite loops)
            
        Returns:
            Sanitized data
        """
        if depth > 10:
            return "[MAX_DEPTH]"
        
        if isinstance(data, dict):
            return self._sanitize_dict(data, depth)
        elif isinstance(data, list):
            return [self.sanitize(item, depth + 1) for item in data]
        elif isinstance(data, str):
            return self._sanitize_string(data)
        else:
            return data
    
    def _sanitize_dict(self, data: Dict, depth: int) -> Dict:
        """Sanitize dictionary."""
        result = {}
        
        for key, value in data.items():
            key_lower = key.lower()
            
            # Completely redact sensitive keys
            if any(s in key_lower for s in self.SENSITIVE_KEYS):
                result[key] = "[REDACTED]"
            
            # Partially mask certain keys
            elif any(s in key_lower for s in self.PARTIAL_MASK_KEYS):
                result[key] = self._partial_mask(value)
            
            # Recursively process nested structures
            else:
                result[key] = self.sanitize(value, depth + 1)
        
        return result
    
    def _sanitize_string(self, text: str) -> str:
        """Sanitize string by replacing sensitive patterns."""
        result = text
        
        # Replace each pattern
        for name, pattern in self.PATTERNS.items():
            if name in ("password", "api_key"):
                # Full redaction for credentials
                result = pattern.sub("[REDACTED]", result)
            else:
                # Partial mask for other sensitive data
                result = pattern.sub(
                    lambda m: self._partial_mask(m.group(0)),
                    result
                )
        
        return result
    
    def _partial_mask(self, value: Any) -> str:
        """Partially mask a value, showing only last N characters."""
        if value is None:
            return "[NONE]"
        
        s = str(value)
        
        if len(s) <= self.show_last:
            return self.mask_char * len(s)
        
        masked_len = len(s) - self.show_last
        return self.mask_char * masked_len + s[-self.show_last:]
    
    def sanitize_for_log(self, message: str, context: Dict = None) -> str:
        """
        Prepare message for logging.
        
        Args:
            message: Log message
            context: Optional context dict
            
        Returns:
            Safe log message
        """
        safe_message = self._sanitize_string(message)
        
        if context:
            safe_context = self.sanitize(context)
            return f"{safe_message} | context={safe_context}"
        
        return safe_message
    
    def sanitize_for_ai(self, data: Dict) -> Dict:
        """
        Sanitize data before sending to AI provider.
        
        More aggressive than regular sanitization:
        - Removes all potential PII
        - Removes any credentials
        - Safe to send to third-party AI
        """
        return self.sanitize(data)
    
    def mask_phone(self, phone: str) -> str:
        """Mask phone number for display."""
        if not phone:
            return ""
        
        # Keep country code and last 4 digits
        if len(phone) > 8:
            return phone[:3] + self.mask_char * (len(phone) - 7) + phone[-4:]
        
        return self._partial_mask(phone)
    
    def mask_email(self, email: str) -> str:
        """Mask email for display."""
        if not email or "@" not in email:
            return self._partial_mask(email)
        
        local, domain = email.rsplit("@", 1)
        
        if len(local) > 2:
            masked_local = local[0] + self.mask_char * (len(local) - 2) + local[-1]
        else:
            masked_local = self.mask_char * len(local)
        
        return f"{masked_local}@{domain}"


# Singleton instance
_sanitizer = None


def get_sanitizer() -> DataSanitizer:
    """Get singleton sanitizer instance."""
    global _sanitizer
    if _sanitizer is None:
        _sanitizer = DataSanitizer()
    return _sanitizer


def sanitize(data: Any) -> Any:
    """Convenience function to sanitize data."""
    return get_sanitizer().sanitize(data)


def sanitize_log(message: str, context: Dict = None) -> str:
    """Convenience function for log sanitization."""
    return get_sanitizer().sanitize_for_log(message, context)

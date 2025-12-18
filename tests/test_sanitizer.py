"""
Tests for DataSanitizer
Version: 11.0

Tests sensitive data sanitization.
"""

import pytest
from services.sanitizer import DataSanitizer, sanitize, sanitize_log


class TestDataSanitizer:
    """Test DataSanitizer class."""
    
    @pytest.fixture
    def sanitizer(self):
        return DataSanitizer()
    
    # ========================================================================
    # STRING SANITIZATION
    # ========================================================================
    
    def test_sanitize_phone_number(self, sanitizer):
        """Phone numbers should be partially masked."""
        text = "Call me at +385991234567"
        result = sanitizer._sanitize_string(text)
        
        assert "+385991234567" not in result
        assert "4567" in result  # Last 4 visible
    
    def test_sanitize_email(self, sanitizer):
        """Email addresses should be partially masked."""
        text = "Contact john.doe@example.com"
        result = sanitizer._sanitize_string(text)
        
        assert "john.doe@example.com" not in result
        assert ".com" in result  # Domain partially visible
    
    def test_sanitize_jwt_token(self, sanitizer):
        """JWT tokens should be masked."""
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        text = f"Token: {token}"
        result = sanitizer._sanitize_string(text)
        
        assert token not in result
    
    def test_sanitize_password_field(self, sanitizer):
        """Password fields should be completely redacted."""
        text = 'password="mysecretpassword"'
        result = sanitizer._sanitize_string(text)
        
        assert "mysecretpassword" not in result
        assert "[REDACTED]" in result
    
    def test_sanitize_api_key(self, sanitizer):
        """API keys should be completely redacted."""
        text = "api_key=sk-1234567890abcdefghijklmnop"
        result = sanitizer._sanitize_string(text)
        
        assert "sk-1234567890" not in result
        assert "[REDACTED]" in result
    
    # ========================================================================
    # DICT SANITIZATION
    # ========================================================================
    
    def test_sanitize_dict_sensitive_keys(self, sanitizer):
        """Sensitive keys should be redacted."""
        data = {
            "username": "john",
            "password": "secret123",
            "api_key": "sk-12345"
        }
        result = sanitizer.sanitize(data)
        
        assert result["username"] == "john"
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
    
    def test_sanitize_dict_partial_mask_keys(self, sanitizer):
        """Phone and email keys should be partially masked."""
        data = {
            "phone": "+385991234567",
            "email": "test@example.com"
        }
        result = sanitizer.sanitize(data)
        
        assert "+385991234567" not in result["phone"]
        assert "4567" in result["phone"]
    
    def test_sanitize_nested_dict(self, sanitizer):
        """Nested structures should be sanitized recursively."""
        data = {
            "user": {
                "name": "John",
                "credentials": {
                    "password": "secret",
                    "token": "abc123"
                }
            }
        }
        result = sanitizer.sanitize(data)
        
        assert result["user"]["name"] == "John"
        assert result["user"]["credentials"]["password"] == "[REDACTED]"
        assert result["user"]["credentials"]["token"] == "[REDACTED]"
    
    def test_sanitize_list(self, sanitizer):
        """Lists should be sanitized."""
        data = [
            {"password": "secret1"},
            {"password": "secret2"}
        ]
        result = sanitizer.sanitize(data)
        
        assert result[0]["password"] == "[REDACTED]"
        assert result[1]["password"] == "[REDACTED]"
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    def test_mask_phone(self, sanitizer):
        """Phone masking should show last 4 digits."""
        result = sanitizer.mask_phone("+385991234567")
        
        assert "4567" in result
        assert "9912" not in result
    
    def test_mask_email(self, sanitizer):
        """Email masking should preserve domain."""
        result = sanitizer.mask_email("john.doe@example.com")
        
        assert "@example.com" in result
        assert "john.doe" not in result
    
    def test_mask_empty_values(self, sanitizer):
        """Empty values should be handled gracefully."""
        assert sanitizer.mask_phone("") == ""
        assert sanitizer.mask_phone(None) == ""
        assert sanitizer.mask_email("") == ""
    
    # ========================================================================
    # CONVENIENCE FUNCTIONS
    # ========================================================================
    
    def test_sanitize_function(self):
        """Test convenience sanitize function."""
        data = {"password": "secret"}
        result = sanitize(data)
        
        assert result["password"] == "[REDACTED]"
    
    def test_sanitize_log_function(self):
        """Test convenience log sanitization."""
        message = "User logged in with password=secret123"
        result = sanitize_log(message)
        
        assert "secret123" not in result
        assert "[REDACTED]" in result
    
    def test_sanitize_log_with_context(self):
        """Test log sanitization with context."""
        message = "Processing request"
        context = {"api_key": "sk-12345"}
        result = sanitize_log(message, context)
        
        assert "sk-12345" not in result
        assert "[REDACTED]" in result
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_max_depth_protection(self, sanitizer):
        """Deep nesting should be protected."""
        # Create deeply nested structure
        data = {"level": 0}
        current = data
        for i in range(15):
            current["nested"] = {"level": i + 1}
            current = current["nested"]
        
        result = sanitizer.sanitize(data)
        
        # Should not crash and should have max depth marker
        assert "[MAX_DEPTH]" in str(result)
    
    def test_none_values(self, sanitizer):
        """None values should be handled."""
        data = {"field": None}
        result = sanitizer.sanitize(data)
        
        assert result["field"] is None
    
    def test_non_string_values(self, sanitizer):
        """Non-string values should pass through."""
        data = {
            "count": 42,
            "active": True,
            "ratio": 3.14
        }
        result = sanitizer.sanitize(data)
        
        assert result["count"] == 42
        assert result["active"] is True
        assert result["ratio"] == 3.14

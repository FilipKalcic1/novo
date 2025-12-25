"""
Error Translation Service
Version: 1.0

UNIFIED ERROR HANDLING - Consolidates all error translation logic.

This module replaces scattered hardcoded error messages with:
1. Pattern-based error detection
2. Context-aware error messages
3. AI feedback generation for self-correction
4. Learning from errors to improve suggestions

Moves ~200 lines of hardcoded error logic from message_engine.py
into a dedicated, learnable service.
"""

import logging
import re
import json
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

ERROR_PATTERNS_FILE = Path.cwd() / ".cache" / "error_patterns.json"


@dataclass
class ErrorPattern:
    """Pattern for detecting and translating errors."""
    pattern: str  # Regex pattern
    user_message_template: str  # Message shown to user
    ai_feedback_template: str  # Feedback for AI self-correction
    category: str  # Error category (permission, validation, network, etc.)
    context_keywords: List[str] = field(default_factory=list)  # Tool keywords for context
    occurrence_count: int = 0
    last_seen: Optional[str] = None
    resolution_hints: List[str] = field(default_factory=list)

    def matches(self, error: str, tool_name: str = "") -> bool:
        """Check if this pattern matches the error."""
        error_lower = error.lower()
        tool_lower = tool_name.lower()

        # Check regex pattern
        if not re.search(self.pattern, error_lower, re.IGNORECASE):
            return False

        # If context keywords specified, at least one must match tool name
        if self.context_keywords:
            if not any(kw in tool_lower for kw in self.context_keywords):
                return False

        return True

    def format_user_message(self, error: str, tool_name: str) -> str:
        """Format user message with context."""
        msg = self.user_message_template
        msg = msg.replace("{tool_name}", tool_name)
        msg = msg.replace("{error}", error[:200])  # Truncate long errors
        return msg

    def format_ai_feedback(self, error: str, tool_name: str) -> str:
        """Format AI feedback with context."""
        feedback = self.ai_feedback_template
        feedback = feedback.replace("{tool_name}", tool_name)
        feedback = feedback.replace("{error}", error[:200])
        return feedback


class ErrorTranslator:
    """
    Unified error translation service.

    Features:
    1. Pattern-based error detection (no hardcoded if/else chains)
    2. Context-aware messages (different message for booking vs generic)
    3. AI feedback generation
    4. Learning from errors (frequency tracking)
    """

    def __init__(self):
        """Initialize error translator."""
        self.patterns: List[ErrorPattern] = []
        self._load_default_patterns()
        self._load_learned_patterns()

    def _load_default_patterns(self) -> None:
        """
        Load default error patterns.

        These are the base patterns - they can be extended/overridden
        by learned patterns from runtime experience.
        """
        self.patterns = [
            # Permission errors (403)
            ErrorPattern(
                pattern=r"permission|403|forbidden|unauthorized|access denied",
                user_message_template=(
                    "❌ Nemate dozvolu za rezervaciju ovog vozila.\n\n"
                    "Mogući razlozi:\n"
                    "• Vozilo nije dostupno za vašu grupu korisnika\n"
                    "• Već postoji aktivna rezervacija za taj termin\n"
                    "• Vaš korisnički račun nema prava za rezervacije\n\n"
                    "Kontaktirajte administratora za više informacija."
                ),
                ai_feedback_template=(
                    "User lacks permission for {tool_name}. "
                    "Do not retry with same parameters. "
                    "Suggest user contact administrator or try different resource."
                ),
                category="permission",
                context_keywords=["booking", "calendar", "reservation", "vehicle"]
            ),

            # Generic permission error (no context)
            ErrorPattern(
                pattern=r"permission|403|forbidden|unauthorized|access denied",
                user_message_template=(
                    "❌ Nemate dozvolu za ovu operaciju.\n"
                    "Kontaktirajte administratora ako mislite da je ovo greška."
                ),
                ai_feedback_template=(
                    "Permission denied for {tool_name}. "
                    "User cannot perform this action."
                ),
                category="permission",
                context_keywords=[]  # Generic - matches any tool
            ),

            # Not found (404)
            ErrorPattern(
                pattern=r"not found|404|ne postoji|nije pronađen",
                user_message_template=(
                    "❌ Traženi resurs nije pronađen.\n"
                    "Možete li ponoviti zahtjev s drugim parametrima?"
                ),
                ai_feedback_template=(
                    "Resource not found for {tool_name}. "
                    "The ID or reference provided does not exist. "
                    "Try fetching available items first."
                ),
                category="not_found"
            ),

            # Timeout
            ErrorPattern(
                pattern=r"timeout|timed out|istekao|connection.*reset",
                user_message_template=(
                    "⏱️ Zahtjev je istekao.\n"
                    "Molim pokušajte ponovno za trenutak."
                ),
                ai_feedback_template=(
                    "Request timed out for {tool_name}. "
                    "This is temporary. Retry the same request."
                ),
                category="timeout"
            ),

            # Network errors
            ErrorPattern(
                pattern=r"connection|network|dns|unreachable|no route",
                user_message_template=(
                    "🌐 Problem s mrežom.\n"
                    "Molim pokušajte ponovno."
                ),
                ai_feedback_template=(
                    "Network error for {tool_name}. "
                    "This is temporary infrastructure issue. Retry in a moment."
                ),
                category="network"
            ),

            # Validation errors
            ErrorPattern(
                pattern=r"validation|invalid|nevažeći|required|nedostaje|missing",
                user_message_template=(
                    "⚠️ Nevažeći podaci.\n"
                    "Provjerite unesene informacije i pokušajte ponovno."
                ),
                ai_feedback_template=(
                    "Validation error for {tool_name}: {error}. "
                    "Check required parameters and their formats."
                ),
                category="validation"
            ),

            # Unknown filter field (specific to our API)
            ErrorPattern(
                pattern=r"unknown filter field",
                user_message_template=(
                    "⚠️ Tehnički problem s pretragom.\n"
                    "Pokušavam drugačije..."
                ),
                ai_feedback_template=(
                    "Filter field not supported for {tool_name}. "
                    "This endpoint doesn't support the filter you tried. "
                    "Use a different approach - maybe direct parameter or different tool."
                ),
                category="filter_error"
            ),

            # Rate limiting
            ErrorPattern(
                pattern=r"rate limit|too many requests|429|throttl",
                user_message_template=(
                    "⏳ Sustav je trenutno zauzet.\n"
                    "Pokušajte ponovno za nekoliko sekundi."
                ),
                ai_feedback_template=(
                    "Rate limited. Wait and retry automatically."
                ),
                category="rate_limit"
            ),

            # Server error (500)
            ErrorPattern(
                pattern=r"500|internal server error|server error",
                user_message_template=(
                    "❌ Došlo je do greške na serveru.\n"
                    "Pokušajte ponovno za trenutak."
                ),
                ai_feedback_template=(
                    "Server error for {tool_name}. "
                    "This is backend issue, not user's fault. "
                    "May retry after a moment."
                ),
                category="server_error"
            ),

            # Authentication (401)
            ErrorPattern(
                pattern=r"401|authentication|token.*expired|unauthenticated",
                user_message_template=(
                    "🔐 Problem s prijavom.\n"
                    "Molimo pričekajte dok se sustav ponovno poveže."
                ),
                ai_feedback_template=(
                    "Authentication expired. System will auto-refresh token. Retry."
                ),
                category="auth"
            ),
        ]

    def _load_learned_patterns(self) -> None:
        """Load patterns learned from runtime experience."""
        if not ERROR_PATTERNS_FILE.exists():
            return

        try:
            with open(ERROR_PATTERNS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for pattern_data in data.get("learned_patterns", []):
                # Update existing pattern stats or add new learned patterns
                pattern_str = pattern_data.get("pattern", "")
                for existing in self.patterns:
                    if existing.pattern == pattern_str:
                        existing.occurrence_count = pattern_data.get("occurrence_count", 0)
                        existing.last_seen = pattern_data.get("last_seen")
                        if pattern_data.get("resolution_hints"):
                            existing.resolution_hints = pattern_data["resolution_hints"]
                        break

            logger.info(f"Loaded error patterns from cache")

        except Exception as e:
            logger.warning(f"Failed to load error patterns: {e}")

    def translate(
        self,
        error: str,
        tool_name: str = "",
        for_user: bool = True
    ) -> str:
        """
        Translate error to human-readable message.

        Args:
            error: Technical error message
            tool_name: Name of tool that failed
            for_user: If True, return user message; else return AI feedback

        Returns:
            Translated error message
        """
        # Find matching pattern
        for pattern in self.patterns:
            if pattern.matches(error, tool_name):
                # Update stats
                pattern.occurrence_count += 1
                pattern.last_seen = datetime.utcnow().isoformat()

                if for_user:
                    return pattern.format_user_message(error, tool_name)
                else:
                    return pattern.format_ai_feedback(error, tool_name)

        # No pattern matched - return generic message
        if for_user:
            return (
                f"❌ Došlo je do greške kod operacije '{tool_name}'.\n"
                f"Detalji: {error[:200]}\n\n"
                f"Pokušajte reformulirati zahtjev ili kontaktirajte podršku."
            )
        else:
            return f"Error in {tool_name}: {error}. Review parameters and try alternative approach."

    def get_ai_feedback(self, error: str, tool_name: str = "") -> str:
        """Get AI feedback for error (for self-correction)."""
        return self.translate(error, tool_name, for_user=False)

    def get_user_message(self, error: str, tool_name: str = "") -> str:
        """Get user-friendly message for error."""
        return self.translate(error, tool_name, for_user=True)

    def record_resolution(
        self,
        error: str,
        tool_name: str,
        resolution_hint: str
    ) -> None:
        """
        Record how an error was resolved - for learning.

        Args:
            error: Original error
            tool_name: Tool that failed
            resolution_hint: What worked to fix it
        """
        for pattern in self.patterns:
            if pattern.matches(error, tool_name):
                if resolution_hint not in pattern.resolution_hints:
                    pattern.resolution_hints.append(resolution_hint)
                    logger.info(f"Learned resolution: {resolution_hint}")
                break

        # Save periodically (every 10 resolutions)
        total = sum(p.occurrence_count for p in self.patterns)
        if total % 10 == 0:
            self._save_patterns()

    def _save_patterns(self) -> None:
        """Save learned patterns to cache."""
        try:
            ERROR_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "version": "1.0",
                "saved_at": datetime.utcnow().isoformat(),
                "learned_patterns": [
                    {
                        "pattern": p.pattern,
                        "category": p.category,
                        "occurrence_count": p.occurrence_count,
                        "last_seen": p.last_seen,
                        "resolution_hints": p.resolution_hints
                    }
                    for p in self.patterns
                    if p.occurrence_count > 0  # Only save patterns that were used
                ]
            }

            with open(ERROR_PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Failed to save error patterns: {e}")


# Global instance
_error_translator: Optional[ErrorTranslator] = None


def get_error_translator() -> ErrorTranslator:
    """Get global error translator instance."""
    global _error_translator
    if _error_translator is None:
        _error_translator = ErrorTranslator()
    return _error_translator

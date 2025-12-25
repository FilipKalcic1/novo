"""
Error Learning Service - Self-Correction Engine
Version: 1.0

KRITIČNA KOMPONENTA za robustan sustav.

Pamti greške i uči iz njih poput neural networka:
1. Logira greške s kontekstom
2. Prepoznaje uzorke (iste greške se ponavljaju)
3. Automatski primjenjuje popravke za poznate probleme
4. Smanjuje broj ponovljenih grešaka

Primjer:
    Greška: "405 Method Not Allowed" za /automation/MasterData (POST umjesto GET)
    Learning: Sljedeći put kad LLM pokuša POST na MasterData, automatski ispravi na GET
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class ErrorPattern:
    """Represents a learned error pattern."""
    error_code: str
    operation_id: str
    error_message: str
    context: Dict[str, Any]
    correction: Optional[str] = None  # What fixed it
    occurrence_count: int = 1
    last_seen: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    resolved: bool = False


@dataclass
class CorrectionRule:
    """Automatic correction rule learned from errors."""
    trigger_pattern: str  # Regex or exact match
    trigger_operation: Optional[str]  # Specific operation or None for all
    correction_type: str  # "method", "param", "url", "value"
    correction_action: Dict[str, Any]  # What to change
    confidence: float  # 0.0 to 1.0
    success_count: int = 0
    failure_count: int = 0


class ErrorLearningService:
    """
    Self-learning error correction system.

    Features:
    - Pattern detection across errors
    - Automatic correction rules
    - Confidence-based application
    - Redis-backed persistence

    Inspiracija: Neural network backpropagation - greška se propagira
    natrag i sustav se prilagođava.
    """

    # Known error patterns and their corrections
    # This is the "training data" for the system
    KNOWN_CORRECTIONS: List[CorrectionRule] = [
        # 405 errors often mean wrong HTTP method
        CorrectionRule(
            trigger_pattern="405",
            trigger_operation=None,
            correction_type="method",
            correction_action={"from": "POST", "to": "GET", "hint": "retrieval_operation"},
            confidence=0.7,
        ),
        # 400 with "required" often means missing param
        CorrectionRule(
            trigger_pattern="required",
            trigger_operation=None,
            correction_type="param",
            correction_action={"action": "inject_from_context"},
            confidence=0.6,
        ),
        # HTML response means auth issue or wrong URL
        CorrectionRule(
            trigger_pattern="HTML_RESPONSE",
            trigger_operation=None,
            correction_type="url",
            correction_action={"action": "verify_swagger_name"},
            confidence=0.8,
        ),
    ]

    def __init__(self, redis_client: Optional[Any] = None):
        """
        Initialize error learning service.

        Args:
            redis_client: Optional Redis client for persistence
        """
        self.redis = redis_client
        self._error_patterns: Dict[str, ErrorPattern] = {}
        self._correction_rules: List[CorrectionRule] = self.KNOWN_CORRECTIONS.copy()
        self._learned_rules: List[CorrectionRule] = []

        # Statistics
        self._total_errors = 0
        self._corrected_errors = 0
        self._pattern_matches = 0

        logger.info("ErrorLearningService initialized")

    async def record_error(
        self,
        error_code: str,
        operation_id: str,
        error_message: str,
        context: Dict[str, Any],
        was_corrected: bool = False,
        correction: Optional[str] = None
    ) -> None:
        """
        Record an error for learning.

        Args:
            error_code: Error code (HTTP status, internal code, etc.)
            operation_id: Tool/operation that failed
            error_message: Full error message
            context: Context when error occurred (params, user, etc.)
            was_corrected: Whether error was automatically corrected
            correction: What fixed it (if corrected)
        """
        self._total_errors += 1

        # Create pattern key
        pattern_key = f"{error_code}:{operation_id}"

        if pattern_key in self._error_patterns:
            # Update existing pattern
            pattern = self._error_patterns[pattern_key]
            pattern.occurrence_count += 1
            pattern.last_seen = datetime.utcnow().isoformat()
            if correction:
                pattern.correction = correction
                pattern.resolved = True
        else:
            # New pattern
            pattern = ErrorPattern(
                error_code=error_code,
                operation_id=operation_id,
                error_message=error_message,
                context=self._sanitize_context(context),
                correction=correction,
                resolved=was_corrected
            )
            self._error_patterns[pattern_key] = pattern

        # Log for analysis
        logger.info(
            f"📊 Error recorded: {pattern_key} "
            f"(count={pattern.occurrence_count}, resolved={pattern.resolved})"
        )

        # Persist to Redis if available
        if self.redis:
            await self._persist_pattern(pattern_key, pattern)

        # Check for new learnable patterns
        await self._analyze_patterns()

    async def suggest_correction(
        self,
        error_code: str,
        operation_id: str,
        error_message: str,
        current_params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Suggest a correction based on learned patterns.

        Args:
            error_code: Current error code
            operation_id: Failed operation
            error_message: Error message
            current_params: Current parameters

        Returns:
            Correction suggestion or None
        """
        self._pattern_matches += 1

        # Check known corrections first
        for rule in self._correction_rules + self._learned_rules:
            if self._rule_matches(rule, error_code, operation_id, error_message):
                if rule.confidence >= 0.6:  # Only apply confident corrections
                    logger.info(
                        f"💡 Suggesting correction: {rule.correction_type} "
                        f"(confidence={rule.confidence:.2f})"
                    )
                    return {
                        "type": rule.correction_type,
                        "action": rule.correction_action,
                        "confidence": rule.confidence,
                        "rule_pattern": rule.trigger_pattern
                    }

        # Check if we've seen this exact error before and it was resolved
        pattern_key = f"{error_code}:{operation_id}"
        if pattern_key in self._error_patterns:
            pattern = self._error_patterns[pattern_key]
            if pattern.resolved and pattern.correction:
                return {
                    "type": "historical",
                    "action": {"correction": pattern.correction},
                    "confidence": min(0.5 + (pattern.occurrence_count * 0.1), 0.9),
                    "occurrences": pattern.occurrence_count
                }

        return None

    async def apply_correction(
        self,
        correction: Dict[str, Any],
        tool: Any,
        params: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Apply a suggested correction.

        Args:
            correction: Correction suggestion from suggest_correction
            tool: Tool definition
            params: Current parameters
            context: User context

        Returns:
            Corrected parameters or None if not applicable
        """
        correction_type = correction.get("type")
        action = correction.get("action", {})

        if correction_type == "method":
            # This would need tool modification - return hint for caller
            return {
                "hint": "use_get_instead_of_post",
                "original_method": action.get("from"),
                "suggested_method": action.get("to")
            }

        elif correction_type == "param":
            if action.get("action") == "inject_from_context":
                # Try to inject missing params from context
                corrected_params = params.copy()
                for key in ["person_id", "tenant_id", "PersonId", "TenantId"]:
                    if key in context and key not in corrected_params:
                        corrected_params[key] = context[key]
                return {"params": corrected_params}

        elif correction_type == "url":
            if action.get("action") == "verify_swagger_name":
                return {"hint": "check_swagger_name", "message": "URL might be incorrect"}

        elif correction_type == "historical":
            # Return the historical correction
            return {"hint": action.get("correction")}

        return None

    async def report_correction_result(
        self,
        correction: Dict[str, Any],
        success: bool
    ) -> None:
        """
        Report whether a correction worked.

        This is the "backpropagation" - we update rule confidence
        based on success/failure.

        Args:
            correction: The correction that was applied
            success: Whether it worked
        """
        rule_pattern = correction.get("rule_pattern")

        for rule in self._correction_rules + self._learned_rules:
            if rule.trigger_pattern == rule_pattern:
                if success:
                    rule.success_count += 1
                    self._corrected_errors += 1
                    # Increase confidence (max 0.95)
                    rule.confidence = min(
                        rule.confidence + 0.05,
                        0.95
                    )
                else:
                    rule.failure_count += 1
                    # Decrease confidence (min 0.1)
                    rule.confidence = max(
                        rule.confidence - 0.1,
                        0.1
                    )

                logger.info(
                    f"📈 Rule updated: {rule_pattern} "
                    f"(success={success}, new_confidence={rule.confidence:.2f})"
                )
                break

    def get_statistics(self) -> Dict[str, Any]:
        """Get learning statistics."""
        return {
            "total_errors": self._total_errors,
            "corrected_errors": self._corrected_errors,
            "correction_rate": (
                self._corrected_errors / self._total_errors
                if self._total_errors > 0 else 0
            ),
            "pattern_count": len(self._error_patterns),
            "known_rules": len(self._correction_rules),
            "learned_rules": len(self._learned_rules),
            "pattern_matches": self._pattern_matches
        }

    def _rule_matches(
        self,
        rule: CorrectionRule,
        error_code: str,
        operation_id: str,
        error_message: str
    ) -> bool:
        """Check if a rule matches the current error."""
        # Check operation filter
        if rule.trigger_operation and rule.trigger_operation != operation_id:
            return False

        # Check pattern in error code or message
        pattern = rule.trigger_pattern.lower()
        return (
            pattern in str(error_code).lower() or
            pattern in error_message.lower()
        )

    def _sanitize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from context before storing."""
        sensitive_keys = ["token", "password", "secret", "api_key", "auth"]
        return {
            k: "[REDACTED]" if any(s in k.lower() for s in sensitive_keys) else v
            for k, v in context.items()
        }

    async def _persist_pattern(self, key: str, pattern: ErrorPattern) -> None:
        """Persist pattern to Redis."""
        if not self.redis:
            return

        try:
            await self.redis.hset(
                "error_learning:patterns",
                key,
                json.dumps(asdict(pattern))
            )
        except Exception as e:
            logger.warning(f"Failed to persist error pattern: {e}")

    async def _analyze_patterns(self) -> None:
        """
        Analyze patterns to learn new correction rules.

        This is where the "learning" happens - we look for
        patterns in resolved errors and create new rules.
        """
        # Find patterns that were resolved multiple times with same correction
        resolved_patterns = [
            p for p in self._error_patterns.values()
            if p.resolved and p.correction and p.occurrence_count >= 3
        ]

        for pattern in resolved_patterns:
            # Check if we already have a rule for this
            existing = any(
                r.trigger_pattern == pattern.error_code and
                r.trigger_operation == pattern.operation_id
                for r in self._learned_rules
            )

            if not existing:
                # Create new learned rule
                new_rule = CorrectionRule(
                    trigger_pattern=pattern.error_code,
                    trigger_operation=pattern.operation_id,
                    correction_type="historical",
                    correction_action={"correction": pattern.correction},
                    confidence=0.6 + min(pattern.occurrence_count * 0.05, 0.3),
                    success_count=pattern.occurrence_count
                )
                self._learned_rules.append(new_rule)

                logger.info(
                    f"🎓 Learned new rule: {pattern.error_code} → "
                    f"{pattern.correction} (from {pattern.occurrence_count} occurrences)"
                )

    async def load_from_redis(self) -> None:
        """Load persisted patterns from Redis."""
        if not self.redis:
            return

        try:
            patterns = await self.redis.hgetall("error_learning:patterns")
            for key, value in patterns.items():
                data = json.loads(value)
                self._error_patterns[key] = ErrorPattern(**data)

            logger.info(f"Loaded {len(self._error_patterns)} error patterns from Redis")
        except Exception as e:
            logger.warning(f"Failed to load error patterns: {e}")

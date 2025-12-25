"""
Tool Evaluator - Performance Tracking & Learning
Version: 1.0

KRITIČNA KOMPONENTA za kvalitetu sustava.

Prati:
1. Uspješnost alata (success rate)
2. Točnost rezultata (user feedback)
3. Vrijeme izvršenja
4. Učestalost grešaka

Omogućava:
- Penaliziranje alata koji daju krive rezultate
- Boost alata koji rade dobro
- Učenje iz grešaka
- Statistike za debugging

PRIMJER KORIŠTENJA:
    evaluator = get_tool_evaluator()

    # Nakon uspješnog poziva
    evaluator.record_success("get_MasterData", response_time=0.5)

    # Nakon neuspješnog poziva
    evaluator.record_failure("get_Vehicles", "Wrong data returned")

    # Dohvat score-a za prioritizaciju
    score = evaluator.get_score("get_MasterData")  # 0.0 - 1.0
"""

import logging
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

EVALUATION_CACHE_FILE = Path.cwd() / ".cache" / "tool_evaluations.json"


@dataclass
class ToolMetrics:
    """Metrics for a single tool."""
    operation_id: str

    # Success/Failure tracking
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0

    # User feedback tracking
    positive_feedback: int = 0
    negative_feedback: int = 0

    # Error tracking
    error_types: Dict[str, int] = field(default_factory=dict)
    last_error: Optional[str] = None
    last_error_time: Optional[str] = None

    # Performance tracking
    avg_response_time_ms: float = 0.0
    total_response_time_ms: float = 0.0

    # Timestamps
    first_call: Optional[str] = None
    last_call: Optional[str] = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate (0.0 - 1.0)."""
        if self.total_calls == 0:
            return 0.5  # Neutral for new tools
        return self.successful_calls / self.total_calls

    @property
    def user_satisfaction(self) -> float:
        """Calculate user satisfaction (0.0 - 1.0)."""
        total_feedback = self.positive_feedback + self.negative_feedback
        if total_feedback == 0:
            return 0.5  # Neutral if no feedback
        return self.positive_feedback / total_feedback

    @property
    def overall_score(self) -> float:
        """
        Calculate overall tool score (0.0 - 1.0).

        Combines:
        - Success rate (60% weight)
        - User satisfaction (30% weight)
        - Recency penalty (10% weight) - penalize tools with recent errors
        """
        score = 0.0

        # Success rate (60%)
        score += self.success_rate * 0.6

        # User satisfaction (30%)
        score += self.user_satisfaction * 0.3

        # Recency bonus/penalty (10%)
        if self.last_error_time:
            try:
                last_error = datetime.fromisoformat(self.last_error_time)
                hours_since_error = (datetime.utcnow() - last_error).total_seconds() / 3600

                if hours_since_error < 1:
                    # Recent error - penalty
                    score += 0.0
                elif hours_since_error < 24:
                    # Error within day - small penalty
                    score += 0.05
                else:
                    # Old error - no penalty
                    score += 0.10
            except (ValueError, TypeError):
                score += 0.05  # Default
        else:
            # No errors ever - bonus
            score += 0.10

        return min(1.0, max(0.0, score))

    def to_dict(self) -> Dict:
        return {
            "operation_id": self.operation_id,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "positive_feedback": self.positive_feedback,
            "negative_feedback": self.negative_feedback,
            "error_types": self.error_types,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "avg_response_time_ms": self.avg_response_time_ms,
            "total_response_time_ms": self.total_response_time_ms,
            "first_call": self.first_call,
            "last_call": self.last_call,
            "success_rate": self.success_rate,
            "user_satisfaction": self.user_satisfaction,
            "overall_score": self.overall_score
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ToolMetrics":
        return cls(
            operation_id=data["operation_id"],
            total_calls=data.get("total_calls", 0),
            successful_calls=data.get("successful_calls", 0),
            failed_calls=data.get("failed_calls", 0),
            positive_feedback=data.get("positive_feedback", 0),
            negative_feedback=data.get("negative_feedback", 0),
            error_types=data.get("error_types", {}),
            last_error=data.get("last_error"),
            last_error_time=data.get("last_error_time"),
            avg_response_time_ms=data.get("avg_response_time_ms", 0.0),
            total_response_time_ms=data.get("total_response_time_ms", 0.0),
            first_call=data.get("first_call"),
            last_call=data.get("last_call")
        )


class ToolEvaluator:
    """
    Tool performance evaluator with learning capabilities.

    Features:
    1. Track success/failure rates per tool
    2. Learn from user feedback
    3. Apply penalties for poor performance
    4. Boost well-performing tools
    5. Persist evaluations across restarts
    """

    def __init__(self):
        self.metrics: Dict[str, ToolMetrics] = {}
        self._load_from_cache()

    def _load_from_cache(self) -> None:
        """Load metrics from cache file."""
        if not EVALUATION_CACHE_FILE.exists():
            logger.info("No tool evaluations cache found - starting fresh")
            return

        try:
            with open(EVALUATION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for metric_data in data.get("metrics", []):
                metric = ToolMetrics.from_dict(metric_data)
                self.metrics[metric.operation_id] = metric

            logger.info(f"Loaded evaluations for {len(self.metrics)} tools")
        except Exception as e:
            logger.warning(f"Failed to load tool evaluations: {e}")

    def _save_to_cache(self) -> None:
        """Save metrics to cache file."""
        try:
            EVALUATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "version": "1.0",
                "saved_at": datetime.utcnow().isoformat(),
                "metrics": [m.to_dict() for m in self.metrics.values()]
            }

            with open(EVALUATION_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Failed to save tool evaluations: {e}")

    def _get_or_create_metrics(self, operation_id: str) -> ToolMetrics:
        """Get or create metrics for a tool."""
        if operation_id not in self.metrics:
            self.metrics[operation_id] = ToolMetrics(operation_id=operation_id)
        return self.metrics[operation_id]

    def record_success(
        self,
        operation_id: str,
        response_time_ms: float = 0.0,
        params_used: Optional[Dict] = None
    ) -> None:
        """
        Record successful tool execution.

        Args:
            operation_id: Tool that succeeded
            response_time_ms: Response time in milliseconds
            params_used: Parameters that were used (for learning)
        """
        metrics = self._get_or_create_metrics(operation_id)

        now = datetime.utcnow().isoformat()

        metrics.total_calls += 1
        metrics.successful_calls += 1
        metrics.last_call = now

        if not metrics.first_call:
            metrics.first_call = now

        # Update response time average
        if response_time_ms > 0:
            metrics.total_response_time_ms += response_time_ms
            metrics.avg_response_time_ms = (
                metrics.total_response_time_ms / metrics.total_calls
            )

        logger.debug(
            f"✅ Recorded success for {operation_id} "
            f"(rate: {metrics.success_rate:.1%}, score: {metrics.overall_score:.2f})"
        )

        # Save periodically (every 10 calls)
        if metrics.total_calls % 10 == 0:
            self._save_to_cache()

    def record_failure(
        self,
        operation_id: str,
        error_message: str,
        error_type: str = "unknown",
        params_used: Optional[Dict] = None
    ) -> None:
        """
        Record failed tool execution.

        Args:
            operation_id: Tool that failed
            error_message: Error message
            error_type: Error category (validation, permission, network, etc.)
            params_used: Parameters that were used
        """
        metrics = self._get_or_create_metrics(operation_id)

        now = datetime.utcnow().isoformat()

        metrics.total_calls += 1
        metrics.failed_calls += 1
        metrics.last_call = now
        metrics.last_error = error_message[:200]
        metrics.last_error_time = now

        if not metrics.first_call:
            metrics.first_call = now

        # Track error types
        if error_type not in metrics.error_types:
            metrics.error_types[error_type] = 0
        metrics.error_types[error_type] += 1

        logger.info(
            f"❌ Recorded failure for {operation_id}: {error_type} "
            f"(rate: {metrics.success_rate:.1%}, score: {metrics.overall_score:.2f})"
        )

        # Save on every failure (more important to persist)
        self._save_to_cache()

    def record_user_feedback(
        self,
        operation_id: str,
        positive: bool,
        feedback_text: Optional[str] = None
    ) -> None:
        """
        Record user feedback for tool result.

        This is KEY for learning - users can indicate when results are wrong.

        Args:
            operation_id: Tool that produced the result
            positive: True if user accepted result, False if they complained
            feedback_text: Optional user feedback text
        """
        metrics = self._get_or_create_metrics(operation_id)

        if positive:
            metrics.positive_feedback += 1
            logger.debug(f"👍 Positive feedback for {operation_id}")
        else:
            metrics.negative_feedback += 1
            logger.info(
                f"👎 Negative feedback for {operation_id}: {feedback_text or 'no text'}"
            )

        self._save_to_cache()

    def get_score(self, operation_id: str) -> float:
        """
        Get overall score for a tool (0.0 - 1.0).

        Higher score = better performance.

        Args:
            operation_id: Tool to score

        Returns:
            Score from 0.0 (worst) to 1.0 (best)
        """
        if operation_id not in self.metrics:
            return 0.5  # Neutral for unknown tools

        return self.metrics[operation_id].overall_score

    def get_penalty(self, operation_id: str) -> float:
        """
        Get penalty adjustment for tool score.

        Returns negative value to subtract from base similarity score.

        Args:
            operation_id: Tool to penalize

        Returns:
            Penalty (0.0 to 0.2) to subtract from score
        """
        score = self.get_score(operation_id)

        # Invert score to get penalty
        # score 1.0 → penalty 0.0
        # score 0.5 → penalty 0.1
        # score 0.0 → penalty 0.2
        penalty = (1.0 - score) * 0.2

        return penalty

    def get_boost(self, operation_id: str) -> float:
        """
        Get boost adjustment for tool score.

        Returns positive value to add to base similarity score.

        Args:
            operation_id: Tool to boost

        Returns:
            Boost (0.0 to 0.1) to add to score
        """
        score = self.get_score(operation_id)

        # High performers get boost
        # score 1.0 → boost 0.1
        # score 0.5 → boost 0.0
        # score 0.0 → boost 0.0
        if score > 0.7:
            boost = (score - 0.7) / 3.0  # Max 0.1
            return boost

        return 0.0

    def apply_evaluation_adjustment(
        self,
        operation_id: str,
        base_score: float
    ) -> float:
        """
        Apply evaluation-based adjustment to tool score.

        Args:
            operation_id: Tool being scored
            base_score: Base similarity score

        Returns:
            Adjusted score
        """
        boost = self.get_boost(operation_id)
        penalty = self.get_penalty(operation_id)

        adjusted = base_score + boost - penalty

        if boost > 0 or penalty > 0:
            logger.debug(
                f"📊 {operation_id}: {base_score:.3f} "
                f"+ {boost:.3f} - {penalty:.3f} = {adjusted:.3f}"
            )

        return max(0.0, min(1.0, adjusted))

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall statistics for debugging."""
        if not self.metrics:
            return {"message": "No tool evaluations yet"}

        total_calls = sum(m.total_calls for m in self.metrics.values())
        total_success = sum(m.successful_calls for m in self.metrics.values())
        total_failures = sum(m.failed_calls for m in self.metrics.values())

        # Top performers
        sorted_by_score = sorted(
            self.metrics.values(),
            key=lambda m: m.overall_score,
            reverse=True
        )

        top_5 = [
            {"tool": m.operation_id, "score": m.overall_score, "calls": m.total_calls}
            for m in sorted_by_score[:5]
        ]

        # Worst performers
        bottom_5 = [
            {"tool": m.operation_id, "score": m.overall_score, "calls": m.total_calls}
            for m in sorted_by_score[-5:]
            if m.total_calls > 0
        ]

        return {
            "total_tools_tracked": len(self.metrics),
            "total_calls": total_calls,
            "overall_success_rate": total_success / total_calls if total_calls > 0 else 0,
            "total_failures": total_failures,
            "top_performers": top_5,
            "worst_performers": bottom_5
        }


# Global instance
_tool_evaluator: Optional[ToolEvaluator] = None


def get_tool_evaluator() -> ToolEvaluator:
    """Get global tool evaluator instance."""
    global _tool_evaluator
    if _tool_evaluator is None:
        _tool_evaluator = ToolEvaluator()
    return _tool_evaluator

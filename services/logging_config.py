"""
Structured JSON Logging Configuration
Version: 1.0.0

Provides Kubernetes-compatible JSON logging with:
- Structured fields for easy parsing
- Trace ID propagation for distributed tracing
- Log level filtering
- Performance metrics in logs
"""
import logging
import sys
import os
from datetime import datetime, timezone
from typing import Optional
from contextvars import ContextVar

import structlog


# Context variable for trace ID (propagated across async calls)
trace_id_var: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)


def get_trace_id() -> Optional[str]:
    """Get current trace ID from context."""
    return trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """Set trace ID in context."""
    trace_id_var.set(trace_id)


def add_trace_id(logger, method_name, event_dict):
    """Structlog processor to add trace_id to all log entries."""
    trace_id = get_trace_id()
    if trace_id:
        event_dict['trace_id'] = trace_id
    return event_dict


def add_timestamp(logger, method_name, event_dict):
    """Add ISO format timestamp."""
    event_dict['timestamp'] = datetime.now(timezone.utc).isoformat()
    return event_dict


def add_service_info(logger, method_name, event_dict):
    """Add service metadata."""
    event_dict['service'] = os.getenv('APP_NAME', 'novo-bot')
    event_dict['version'] = os.getenv('APP_VERSION', 'unknown')
    event_dict['environment'] = os.getenv('APP_ENV', 'development')
    return event_dict


def rename_event_key(logger, method_name, event_dict):
    """Rename 'event' to 'message' for better compatibility."""
    if 'event' in event_dict:
        event_dict['message'] = event_dict.pop('event')
    return event_dict


def configure_logging(
    json_format: bool = True,
    log_level: str = "INFO"
) -> None:
    """
    Configure structured logging for the application.

    Args:
        json_format: If True, output JSON logs (for Kubernetes).
                    If False, output colored console logs (for development).
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Determine format based on environment
    if json_format is None:
        json_format = os.getenv('APP_ENV', 'development') == 'production'

    # Common processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_trace_id,
        add_timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        # JSON format for Kubernetes/production
        shared_processors.extend([
            add_service_info,
            rename_event_key,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ])
    else:
        # Console format for development
        shared_processors.extend([
            structlog.dev.ConsoleRenderer(colors=True)
        ])

    # Configure structlog
    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    # Reduce noise from verbose libraries
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('openai').setLevel(logging.WARNING)
    logging.getLogger('azure').setLevel(logging.WARNING)


def get_logger(name: str = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Usage:
        logger = get_logger(__name__)
        logger.info("Processing request", user_id=123, action="login")

    Output (JSON mode):
        {"timestamp": "2024-01-15T10:23:45.123Z", "level": "info",
         "logger": "services.api_gateway", "message": "Processing request",
         "user_id": 123, "action": "login", "trace_id": "abc123"}
    """
    return structlog.get_logger(name)


# Convenience function for timing operations
class LogTimer:
    """Context manager for timing operations and logging duration."""

    def __init__(self, logger, operation: str, **extra):
        self.logger = logger
        self.operation = operation
        self.extra = extra
        self.start_time = None

    def __enter__(self):
        self.start_time = datetime.now(timezone.utc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (datetime.now(timezone.utc) - self.start_time).total_seconds() * 1000

        if exc_type:
            self.logger.error(
                f"{self.operation} failed",
                duration_ms=round(duration_ms, 2),
                error=str(exc_val),
                **self.extra
            )
        else:
            self.logger.info(
                f"{self.operation} completed",
                duration_ms=round(duration_ms, 2),
                **self.extra
            )

        return False  # Don't suppress exceptions

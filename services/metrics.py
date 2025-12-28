"""
Prometheus Metrics Module
Version: 1.0.0

Provides application metrics for monitoring and alerting.

Usage:
    from services.metrics import (
        TOOL_EXECUTION_DURATION,
        TOOL_ERRORS,
        ACTIVE_CONVERSATIONS,
        record_tool_execution
    )

    # Record tool execution time
    with TOOL_EXECUTION_DURATION.labels(tool_name="get_vehicles", status="success").time():
        result = await execute_tool(...)

    # Or use the helper function
    await record_tool_execution("get_vehicles", duration_ms=234, success=True)
"""
from prometheus_client import Counter, Histogram, Gauge, Info, REGISTRY
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response


# =============================================================================
# APPLICATION INFO
# =============================================================================

APP_INFO = Info(
    'novo_app',
    'Application information'
)


# =============================================================================
# REQUEST METRICS
# =============================================================================

REQUEST_DURATION = Histogram(
    'novo_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint', 'status_code'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

REQUEST_COUNT = Counter(
    'novo_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)


# =============================================================================
# TOOL EXECUTION METRICS
# =============================================================================

TOOL_EXECUTION_DURATION = Histogram(
    'novo_tool_execution_duration_seconds',
    'Tool execution duration in seconds',
    ['tool_name', 'status'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

TOOL_EXECUTIONS_TOTAL = Counter(
    'novo_tool_executions_total',
    'Total tool executions',
    ['tool_name', 'status']
)

TOOL_ERRORS = Counter(
    'novo_tool_errors_total',
    'Total tool execution errors',
    ['tool_name', 'error_type']
)


# =============================================================================
# CONVERSATION METRICS
# =============================================================================

ACTIVE_CONVERSATIONS = Gauge(
    'novo_active_conversations',
    'Number of currently active conversations'
)

CONVERSATION_MESSAGES_TOTAL = Counter(
    'novo_conversation_messages_total',
    'Total messages processed',
    ['direction']  # 'inbound' or 'outbound'
)


# =============================================================================
# AI/LLM METRICS
# =============================================================================

LLM_REQUESTS_TOTAL = Counter(
    'novo_llm_requests_total',
    'Total LLM API requests',
    ['model', 'status']
)

LLM_REQUEST_DURATION = Histogram(
    'novo_llm_request_duration_seconds',
    'LLM API request duration',
    ['model'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

LLM_TOKENS_USED = Counter(
    'novo_llm_tokens_total',
    'Total LLM tokens used',
    ['model', 'type']  # type: 'prompt' or 'completion'
)


# =============================================================================
# EMBEDDING METRICS
# =============================================================================

EMBEDDING_REQUESTS_TOTAL = Counter(
    'novo_embedding_requests_total',
    'Total embedding API requests',
    ['status']
)

EMBEDDING_CACHE_HITS = Counter(
    'novo_embedding_cache_hits_total',
    'Embedding cache hits'
)

EMBEDDING_CACHE_MISSES = Counter(
    'novo_embedding_cache_misses_total',
    'Embedding cache misses'
)


# =============================================================================
# EXTERNAL API METRICS
# =============================================================================

EXTERNAL_API_REQUESTS = Counter(
    'novo_external_api_requests_total',
    'Total external API requests',
    ['service', 'endpoint', 'status_code']
)

EXTERNAL_API_DURATION = Histogram(
    'novo_external_api_duration_seconds',
    'External API request duration',
    ['service', 'endpoint'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)


# =============================================================================
# TOKEN MANAGEMENT METRICS
# =============================================================================

TOKEN_REFRESH_TOTAL = Counter(
    'novo_token_refresh_total',
    'Total OAuth token refreshes',
    ['status']
)

TOKEN_CACHE_HITS = Counter(
    'novo_token_cache_hits_total',
    'Token cache hits'
)


# =============================================================================
# QUEUE METRICS
# =============================================================================

QUEUE_SIZE = Gauge(
    'novo_queue_size',
    'Current queue size',
    ['queue_name']
)

QUEUE_PROCESSING_DURATION = Histogram(
    'novo_queue_processing_duration_seconds',
    'Queue message processing duration',
    ['queue_name'],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0]
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def set_app_info(version: str, environment: str):
    """Set application info metrics."""
    APP_INFO.info({
        'version': version,
        'environment': environment
    })


async def record_tool_execution(tool_name: str, duration_seconds: float, success: bool):
    """Record a tool execution with duration and status."""
    status = "success" if success else "error"
    TOOL_EXECUTION_DURATION.labels(tool_name=tool_name, status=status).observe(duration_seconds)
    TOOL_EXECUTIONS_TOTAL.labels(tool_name=tool_name, status=status).inc()


async def record_llm_request(model: str, duration_seconds: float, success: bool,
                              prompt_tokens: int = 0, completion_tokens: int = 0):
    """Record an LLM API request with timing and token usage."""
    status = "success" if success else "error"
    LLM_REQUESTS_TOTAL.labels(model=model, status=status).inc()
    LLM_REQUEST_DURATION.labels(model=model).observe(duration_seconds)

    if prompt_tokens > 0:
        LLM_TOKENS_USED.labels(model=model, type='prompt').inc(prompt_tokens)
    if completion_tokens > 0:
        LLM_TOKENS_USED.labels(model=model, type='completion').inc(completion_tokens)


async def record_external_api_call(service: str, endpoint: str,
                                    status_code: int, duration_seconds: float):
    """Record an external API call."""
    EXTERNAL_API_REQUESTS.labels(
        service=service,
        endpoint=endpoint,
        status_code=str(status_code)
    ).inc()
    EXTERNAL_API_DURATION.labels(service=service, endpoint=endpoint).observe(duration_seconds)


def get_metrics() -> Response:
    """Generate Prometheus metrics response."""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )

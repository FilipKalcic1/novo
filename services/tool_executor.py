"""
Tool Executor - Production-Ready Execution Engine
Version: 2.0

GATE 1: INVISIBLE INJECTION (Merge) - Auto-inject context params
GATE 2: TYPE VALIDATION & CASTING - Strict type checking
GATE 3: CIRCUIT BREAKER - Prevent cascading failures

Features:
- Domain-agnostic execution
- Parameter resolution from multiple sources
- Circuit breaker protection
- Detailed error feedback for AI

NO business logic.
"""

import logging
import time
from typing import Dict, Any, Optional

from services.api_gateway import APIGateway, HttpMethod, APIResponse
from services.tool_contracts import (
    UnifiedToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult
)
from services.parameter_manager import ParameterManager, ParameterValidationError
from services.circuit_breaker import CircuitBreaker, CircuitOpenError
from services.error_parser import ErrorParser
from services.patterns import should_skip_person_id_injection


logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes tools with full validation and safety gates.

    Pipeline:
    1. GATE 1: Parameter resolution (context injection)
    2. GATE 2: Type validation and casting
    3. GATE 3: Circuit breaker check
    4. HTTP call via API Gateway
    5. GATE 5: Error parsing and AI feedback
    """

    def __init__(
        self,
        gateway: APIGateway,
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        Initialize executor.

        Args:
            gateway: API Gateway for HTTP calls
            circuit_breaker: Optional circuit breaker
        """
        self.gateway = gateway
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.param_manager = ParameterManager()

        logger.info("ToolExecutor initialized (v2.0)")

    async def execute(
        self,
        tool: UnifiedToolDefinition,
        llm_params: Dict[str, Any],
        execution_context: ToolExecutionContext
    ) -> ToolExecutionResult:
        """
        Execute tool with full safety pipeline.

        Args:
            tool: Tool definition
            llm_params: Parameters from LLM
            execution_context: Execution context

        Returns:
            ToolExecutionResult
        """
        start_time = time.time()
        operation_id = tool.operation_id

        logger.info(f"🔧 Executing: {operation_id}")

        try:
            # GATE 1 & 2: Parameter resolution and validation
            resolved_params, warnings = self.param_manager.resolve_parameters(
                tool=tool,
                llm_params=llm_params,
                execution_context=execution_context
            )

            if warnings:
                for warning in warnings:
                    logger.warning(f"⚠️ {warning}")

            # Prepare request components
            path, query_params, body = self.param_manager.prepare_request(
                tool=tool,
                params=resolved_params
            )

            # FIX v13.2: INJECT PERSON_ID DIRECTLY INTO QUERY PARAMS
            # This bypasses parameter_manager validation which was filtering out
            # personId because it's not in Swagger definition for most GET tools.
            # The injection happens HERE, not in message_engine, so it goes
            # directly to the API call without being filtered.
            if tool.method == "GET":
                person_id = execution_context.user_context.get("person_id")
                if person_id:
                    # Use APICapabilityRegistry to check if tool supports PersonId
                    from services.api_capabilities import get_capability_registry, ParameterSupport
                    capability_registry = get_capability_registry()

                    should_inject = True

                    if capability_registry:
                        cap = capability_registry.get_capability(tool.operation_id)
                        if cap and cap.supports_person_id == ParameterSupport.NOT_SUPPORTED:
                            # We learned this endpoint doesn't support PersonId
                            should_inject = False
                            logger.debug(
                                f"⏭️ Skipping personId injection for {tool.operation_id} "
                                f"(learned: NOT_SUPPORTED)"
                            )
                    else:
                        # Fallback: centralized skip patterns from patterns.py
                        should_inject = not should_skip_person_id_injection(tool.operation_id)

                    if should_inject:
                        # Initialize query_params if None
                        if query_params is None:
                            query_params = {}

                        # Check if not already present (using schema-based classification)
                        has_person_id = False
                        for param_name, param_def in tool.parameters.items():
                            if param_def.context_key == "person_id" and param_name in query_params:
                                has_person_id = True
                                break

                        if not has_person_id:
                            # Find the correct parameter name using schema-based classification
                            for param_name, param_def in tool.parameters.items():
                                if param_def.context_key == "person_id":
                                    query_params[param_name] = person_id
                                    logger.info(
                                        f"🎯 DIRECT INJECT: {param_name}={person_id[:8]}... "
                                        f"for {tool.operation_id}"
                                    )
                                    break

            # FIX v13.3: Inject EntryType and AssigneeType for VehicleCalendar booking
            # These params have incorrect context_key in Swagger metadata
            if tool.operation_id == "post_VehicleCalendar" and body:
                if "EntryType" not in body:
                    body["EntryType"] = 0  # BOOKING
                    logger.debug("Injected EntryType=0 (BOOKING) for VehicleCalendar")
                if "AssigneeType" not in body:
                    body["AssigneeType"] = 1  # PERSON
                    logger.debug("Injected AssigneeType=1 (PERSON) for VehicleCalendar")

            # Build full URL using STRICT Master Prompt v3.1 formula
            full_url = self._build_url(tool)

            # VALIDATE: HTTP Method and URL construction
            self._validate_http_request(
                method=tool.method,
                url=full_url,
                query_params=query_params,
                body=body,
                operation_id=operation_id
            )

            # Prepare headers
            headers = self._build_headers(
                tool=tool,
                execution_context=execution_context
            )

            # GATE 3: Circuit breaker protection
            endpoint_key = f"{tool.method} {tool.service_name}{path}"

            response = await self.circuit_breaker.call(
                endpoint_key=endpoint_key,
                func=self._make_http_call,
                method=tool.method,
                url=full_url,
                query_params=query_params,
                body=body,
                headers=headers,
                tenant_id=execution_context.user_context.get("tenant_id")
            )

            # Process response
            execution_time = int((time.time() - start_time) * 1000)

            if not response.success:
                # GATE 5: Error parsing
                ai_feedback = ErrorParser.parse_http_error(
                    status_code=response.status_code,
                    response_body=response.data,
                    operation_id=operation_id
                )

                # FIX v13.2: Learn from "Unknown filter field" errors
                # This teaches the system to stop injecting personId for tools that don't support it
                error_msg = response.error_message or ""
                if "unknown filter field" in error_msg.lower():
                    from services.api_capabilities import get_capability_registry
                    cap_registry = get_capability_registry()
                    if cap_registry:
                        cap_registry.record_failure(
                            operation_id=operation_id,
                            error_message=error_msg,
                            params_used=query_params or {}
                        )
                        logger.info(
                            f"📚 LEARNING: {operation_id} doesn't support filter field in error"
                        )

                return ToolExecutionResult(
                    success=False,
                    operation_id=operation_id,
                    error_code=response.error_code or f"HTTP_{response.status_code}",
                    error_message=response.error_message or "Unknown error",
                    ai_feedback=ai_feedback,
                    http_status=response.status_code,
                    execution_time_ms=execution_time
                )

            # Extract output values for chaining
            output_values = self._extract_output_values(
                response.data,
                tool.output_keys
            )

            return ToolExecutionResult(
                success=True,
                operation_id=operation_id,
                data=response.data,
                output_values=output_values,
                http_status=response.status_code,
                execution_time_ms=execution_time
            )

        except ParameterValidationError as e:
            logger.warning(f"Parameter validation failed: {e}")

            ai_feedback = e.to_ai_feedback()

            return ToolExecutionResult(
                success=False,
                operation_id=operation_id,
                error_code="PARAMETER_VALIDATION_ERROR",
                error_message=str(e),
                ai_feedback=ai_feedback,
                missing_params=e.missing_params,  # KRITIČNO: Proslijedi missing_params za auto-chaining
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        except CircuitOpenError as e:
            logger.warning(f"Circuit open: {e}")

            return ToolExecutionResult(
                success=False,
                operation_id=operation_id,
                error_code="CIRCUIT_OPEN",
                error_message=str(e),
                ai_feedback=str(e),  # Already in Croatian
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        except Exception as e:
            logger.error(f"Execution error: {e}", exc_info=True)

            ai_feedback = (
                f"Neočekivana greška pri izvršavanju '{operation_id}': {str(e)}. "
                f"Pokušaj ponovno ili koristi alternativni alat."
            )

            return ToolExecutionResult(
                success=False,
                operation_id=operation_id,
                error_code="EXECUTION_ERROR",
                error_message=str(e),
                ai_feedback=ai_feedback,
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

    async def _make_http_call(
        self,
        method: str,
        url: str,
        query_params: Optional[Dict],
        body: Optional[Dict],
        headers: Dict[str, str],
        tenant_id: Optional[str]
    ) -> APIResponse:
        """Make HTTP call via API Gateway."""
        return await self.gateway.execute(
            method=HttpMethod[method],
            path=url,
            params=query_params,
            body=body,
            headers=headers,
            tenant_id=tenant_id
        )

    def _validate_http_request(
        self,
        method: str,
        url: str,
        query_params: Optional[Dict],
        body: Optional[Dict],
        operation_id: str
    ) -> None:
        """
        Validate HTTP request before execution.

        防护措施:
        - Ensure method is valid (GET/POST/PUT/PATCH/DELETE)
        - Validate URL construction (no missing slashes)
        - Warn about common mistakes (body on GET, no body on POST)

        Raises:
            ParameterValidationError: If request is invalid
        """
        # Validate HTTP method
        valid_methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        if method not in valid_methods:
            raise ParameterValidationError(
                f"Neispravan HTTP metod '{method}' za '{operation_id}'. "
                f"Dozvoljeni metodi: {', '.join(valid_methods)}."
            )

        # Validate URL construction
        if not url or url == "/":
            raise ParameterValidationError(
                f"URL nije pravilno konstruiran za '{operation_id}'. "
                f"Provjerite da li su svi path parametri zamijenjeni."
            )

        # Validate method-body consistency
        if method == "GET" and body:
            logger.warning(
                f"⚠️ GET request sa body parametrima za '{operation_id}'. "
                f"Body će biti ignoriran."
            )

        if method == "POST" and not body and not query_params:
            logger.warning(
                f"⚠️ POST request bez body i query parametara za '{operation_id}'. "
                f"Ovo može biti greška."
            )

        logger.debug(
            f"✅ HTTP Request validated: {method} {url} "
            f"(query={bool(query_params)}, body={bool(body)})"
        )

    def _build_url(self, tool: "UnifiedToolDefinition") -> str:
        """
        Build full URL using STRICT MASTER PROMPT v3.1 formula.

        URL Formula (Anti-Leakage Protocol):
        - If tool has swagger_name: /{swagger_name}/{path}
        - Otherwise: fallback to service_url logic

        This ensures NO 404/405 errors from wrong URL construction.

        Args:
            tool: UnifiedToolDefinition with swagger_name, service_url, path

        Returns:
            Relative URL string (APIGateway will prepend base_url)
        """
        # MASTER PROMPT v3.1: Strict URL construction
        # Formula: base_url + "/" + swagger_name + "/" + path.lstrip('/')

        swagger_name = tool.swagger_name
        path = tool.path
        service_url = tool.service_url

        # Case 1: If path is absolute (complete URL), use it directly
        if path.startswith("http://") or path.startswith("https://"):
            logger.debug(f"Built URL: {path} (absolute path)")
            return path

        # Case 2: STRICT FORMULA - Use swagger_name if available
        if swagger_name:
            # Clean path
            clean_path = path.lstrip("/")
            # Build: /{swagger_name}/{path}
            url = f"/{swagger_name}/{clean_path}"
            logger.debug(f"Built URL: {url} (swagger_name={swagger_name})")
            return url

        # Case 3: Fallback - Use service_url if swagger_name is empty
        if service_url:
            service_url_clean = service_url.rstrip("/")
            clean_path = path.lstrip("/")

            # If service_url is absolute URL
            if service_url_clean.startswith("http://") or service_url_clean.startswith("https://"):
                url = f"{service_url_clean}/{clean_path}"
                logger.debug(f"Built URL: {url} (absolute service_url)")
                return url

            # If service_url is relative (e.g., "/automation")
            url = f"{service_url_clean}/{clean_path}"
            logger.debug(f"Built URL: {url} (relative service_url={service_url_clean})")
            return url

        # Case 4: No swagger_name, no service_url - use path only
        logger.warning(f"⚠️ No swagger_name or service_url for {tool.operation_id}, using path only")
        return path

    def _build_headers(
        self,
        tool: UnifiedToolDefinition,
        execution_context: ToolExecutionContext
    ) -> Dict[str, str]:
        """
        Build HTTP headers.

        Includes:
        - Content-Type
        - Accept
        - Custom headers from context
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # Add custom headers from context
        custom_headers = execution_context.user_context.get("headers", {})
        headers.update(custom_headers)

        return headers

    def _extract_output_values(
        self,
        response_data: Any,
        output_keys: list
    ) -> Dict[str, Any]:
        """
        Extract output values from response for chaining.

        Args:
            response_data: API response
            output_keys: Keys to extract

        Returns:
            Dict of extracted values
        """
        output_values = {}

        if not response_data or not output_keys:
            return output_values

        # Handle different response structures
        if isinstance(response_data, dict):
            # Direct extraction
            for key in output_keys:
                if key in response_data:
                    output_values[key] = response_data[key]
                # Case-insensitive match
                else:
                    for resp_key, resp_value in response_data.items():
                        if resp_key.lower() == key.lower():
                            output_values[key] = resp_value
                            break

            # Check nested 'data' field
            if "data" in response_data and isinstance(response_data["data"], dict):
                for key in output_keys:
                    if key not in output_values and key in response_data["data"]:
                        output_values[key] = response_data["data"][key]

        elif isinstance(response_data, list) and response_data:
            # Take first item if list
            first_item = response_data[0]
            if isinstance(first_item, dict):
                for key in output_keys:
                    if key in first_item:
                        output_values[key] = first_item[key]

        return output_values

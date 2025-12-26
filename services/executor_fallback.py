"""
Executor with Fallback - Robust tool execution with retry and alternatives.
Version: 1.0

Single responsibility: Execute tools with automatic retry, parameter fixing,
and fallback to alternative tools when primary fails.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Callable

from services.tool_contracts import ToolExecutionContext

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for decision making."""
    PARAMETER_ERROR = "parameter_error"      # Wrong/missing parameters - can fix
    AUTH_ERROR = "auth_error"                # 401/403 - try alternative tool
    NOT_FOUND = "not_found"                  # 404 - ask user or try alternative
    RATE_LIMIT = "rate_limit"                # 429 - wait and retry
    SERVER_ERROR = "server_error"            # 500+ - retry once
    VALIDATION_ERROR = "validation_error"    # Bad data format - fix and retry
    UNKNOWN = "unknown"                      # Unknown - report to user


@dataclass
class ExecutionAttempt:
    """Record of a single execution attempt."""
    tool_name: str
    parameters: Dict[str, Any]
    success: bool
    error: Optional[str] = None
    error_category: Optional[ErrorCategory] = None
    http_status: Optional[int] = None
    data: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionResult:
    """Result of execution with all attempts."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    final_tool: Optional[str] = None
    final_params: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    user_message: Optional[str] = None
    attempts: List[ExecutionAttempt] = field(default_factory=list)
    used_fallback: bool = False


class ExecutorWithFallback:
    """
    Executes tools with automatic retry and fallback logic.

    Strategy:
    1. Try primary tool with given parameters
    2. On parameter error: Try to fix parameters and retry
    3. On auth error: Try alternative tool
    4. On not found: Ask user for clarification OR try alternative
    5. On server error: Retry once after delay
    6. Record all attempts for learning
    """

    MAX_RETRIES = 3
    MAX_ALTERNATIVES = 2

    def __init__(self, registry, executor, search_engine=None):
        """
        Initialize executor.

        Args:
            registry: Tool registry for getting tool definitions
            executor: Actual tool executor (ToolExecutor instance)
            search_engine: Optional search engine for finding alternatives
        """
        self.registry = registry
        self.executor = executor
        self.search_engine = search_engine

        # Parameter fix strategies
        self.param_fixers = {
            "VehicleId": self._fix_vehicle_id,
            "PersonId": self._fix_person_id,
            "FromTime": self._fix_datetime,
            "ToTime": self._fix_datetime,
            "from": self._fix_datetime,
            "to": self._fix_datetime,
        }

    async def execute(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        alternatives: Optional[List[str]] = None,
        user_query: Optional[str] = None
    ) -> ExecutionResult:
        """
        Execute tool with retry and fallback logic.

        Args:
            tool_name: Primary tool to execute
            parameters: Tool parameters
            user_context: User context (person_id, vehicle_id, etc.)
            alternatives: List of alternative tool names to try on failure
            user_query: Original user query (for finding alternatives)

        Returns:
            ExecutionResult with success status, data, and attempt history
        """
        result = ExecutionResult(success=False, attempts=[])

        # Get tool definition
        tool = self.registry.get_tool(tool_name)
        if not tool:
            result.error = f"Tool '{tool_name}' not found"
            result.user_message = f"Tehnički problem - alat '{tool_name}' nije pronađen."
            return result

        # Build execution context
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs={},
            conversation_state={}
        )

        # Try primary tool
        current_params = parameters.copy()
        retry_count = 0

        while retry_count < self.MAX_RETRIES:
            attempt = await self._execute_single(
                tool, current_params, execution_context
            )
            result.attempts.append(attempt)

            if attempt.success:
                result.success = True
                result.data = attempt.data
                result.final_tool = tool_name
                result.final_params = current_params
                return result

            # Categorize error and decide action
            category = attempt.error_category or self._categorize_error(
                attempt.error, attempt.http_status
            )

            if category == ErrorCategory.PARAMETER_ERROR:
                # Try to fix parameters
                fixed_params = await self._fix_parameters(
                    tool, current_params, attempt.error, user_context
                )
                if fixed_params and fixed_params != current_params:
                    current_params = fixed_params
                    retry_count += 1
                    logger.info(f"Retry {retry_count} with fixed params: {list(current_params.keys())}")
                    continue
                else:
                    break  # Can't fix, try alternatives

            elif category == ErrorCategory.RATE_LIMIT:
                # Wait and retry
                import asyncio
                await asyncio.sleep(1)
                retry_count += 1
                continue

            elif category == ErrorCategory.SERVER_ERROR:
                # Retry once
                if retry_count == 0:
                    retry_count += 1
                    continue
                else:
                    break

            else:
                # Auth error, not found, validation - try alternatives
                break

        # Try alternatives if primary failed
        if not result.success and alternatives:
            logger.info(f"Primary tool failed, trying {len(alternatives)} alternatives")
            alt_result = await self._try_alternatives(
                alternatives[:self.MAX_ALTERNATIVES],
                parameters,
                user_context,
                execution_context
            )
            if alt_result.success:
                result.success = True
                result.data = alt_result.data
                result.final_tool = alt_result.final_tool
                result.final_params = alt_result.final_params
                result.used_fallback = True
                result.attempts.extend(alt_result.attempts)
                return result

        # If still failed, try to find alternatives via search
        if not result.success and self.search_engine and user_query:
            logger.info("Searching for alternative tools...")
            search_alts = await self._find_alternatives(
                tool_name, user_query, user_context
            )
            if search_alts:
                alt_result = await self._try_alternatives(
                    search_alts[:self.MAX_ALTERNATIVES],
                    parameters,
                    user_context,
                    execution_context
                )
                if alt_result.success:
                    result.success = True
                    result.data = alt_result.data
                    result.final_tool = alt_result.final_tool
                    result.final_params = alt_result.final_params
                    result.used_fallback = True
                    result.attempts.extend(alt_result.attempts)
                    return result

        # Final failure - prepare user message
        last_attempt = result.attempts[-1] if result.attempts else None
        if last_attempt:
            result.error = last_attempt.error
            category = last_attempt.error_category or self._categorize_error(
                last_attempt.error, last_attempt.http_status
            )
            result.user_message = self._get_user_message(category, last_attempt.error, tool_name)

        return result

    async def _execute_single(
        self,
        tool,
        parameters: Dict[str, Any],
        context: ToolExecutionContext
    ) -> ExecutionAttempt:
        """Execute a single tool call."""
        try:
            exec_result = await self.executor.execute(tool, parameters, context)

            if exec_result.success:
                return ExecutionAttempt(
                    tool_name=tool.operation_id,
                    parameters=parameters,
                    success=True,
                    data=exec_result.data
                )
            else:
                category = self._categorize_error(
                    exec_result.error_message,
                    exec_result.http_status
                )
                return ExecutionAttempt(
                    tool_name=tool.operation_id,
                    parameters=parameters,
                    success=False,
                    error=exec_result.error_message,
                    error_category=category,
                    http_status=exec_result.http_status
                )

        except Exception as e:
            logger.error(f"Execution error: {e}")
            return ExecutionAttempt(
                tool_name=tool.operation_id,
                parameters=parameters,
                success=False,
                error=str(e),
                error_category=ErrorCategory.UNKNOWN
            )

    def _categorize_error(
        self,
        error_message: Optional[str],
        http_status: Optional[int]
    ) -> ErrorCategory:
        """Categorize error for decision making."""
        if http_status:
            if http_status == 400:
                return ErrorCategory.PARAMETER_ERROR
            elif http_status == 401:
                return ErrorCategory.AUTH_ERROR
            elif http_status == 403:
                return ErrorCategory.AUTH_ERROR
            elif http_status == 404:
                return ErrorCategory.NOT_FOUND
            elif http_status == 429:
                return ErrorCategory.RATE_LIMIT
            elif http_status >= 500:
                return ErrorCategory.SERVER_ERROR

        if error_message:
            error_lower = error_message.lower()
            if any(x in error_lower for x in ["parameter", "required", "missing", "invalid"]):
                return ErrorCategory.PARAMETER_ERROR
            if any(x in error_lower for x in ["unauthorized", "forbidden", "permission"]):
                return ErrorCategory.AUTH_ERROR
            if "not found" in error_lower:
                return ErrorCategory.NOT_FOUND
            if any(x in error_lower for x in ["validation", "format", "type"]):
                return ErrorCategory.VALIDATION_ERROR

        return ErrorCategory.UNKNOWN

    async def _fix_parameters(
        self,
        tool,
        parameters: Dict[str, Any],
        error: Optional[str],
        user_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Try to fix parameters based on error."""
        fixed = parameters.copy()
        changed = False

        # Extract missing/invalid param from error
        missing_param = self._extract_param_from_error(error)

        if missing_param:
            # Try to get from user context
            value = self._get_from_context(missing_param, user_context)
            if value:
                fixed[missing_param] = value
                changed = True
                logger.info(f"Fixed {missing_param} from context")

            # Try specific fixer
            elif missing_param in self.param_fixers:
                fixer = self.param_fixers[missing_param]
                new_value = await fixer(parameters, user_context)
                if new_value:
                    fixed[missing_param] = new_value
                    changed = True
                    logger.info(f"Fixed {missing_param} with fixer")

        # Check for required params not in current params
        for param_name, param_def in tool.parameters.items():
            if param_def.required and param_name not in fixed:
                value = self._get_from_context(param_name, user_context)
                if value:
                    fixed[param_name] = value
                    changed = True
                    logger.info(f"Added missing required param {param_name}")

        return fixed if changed else None

    def _extract_param_from_error(self, error: Optional[str]) -> Optional[str]:
        """Extract parameter name from error message."""
        if not error:
            return None

        import re

        # Common patterns
        patterns = [
            r"missing.*?['\"](\w+)['\"]",
            r"required.*?['\"](\w+)['\"]",
            r"parameter[s]?.*?['\"](\w+)['\"]",
            r"invalid.*?['\"](\w+)['\"]",
        ]

        for pattern in patterns:
            match = re.search(pattern, error, re.IGNORECASE)
            if match:
                return match.group(1)

        # Known param names
        known_params = ["VehicleId", "PersonId", "FromTime", "ToTime", "BookingId"]
        error_lower = error.lower()
        for param in known_params:
            if param.lower() in error_lower:
                return param

        return None

    def _get_from_context(self, param_name: str, user_context: Dict[str, Any]) -> Optional[Any]:
        """Get parameter value from user context."""
        param_lower = param_name.lower()

        # Direct mappings
        mappings = {
            "personid": "person_id",
            "driverid": "person_id",
            "vehicleid": lambda ctx: ctx.get("vehicle", {}).get("id"),
            "tenantid": "tenant_id",
        }

        if param_lower in mappings:
            mapping = mappings[param_lower]
            if callable(mapping):
                return mapping(user_context)
            return user_context.get(mapping)

        # Try direct match
        if param_name in user_context:
            return user_context[param_name]

        # Try from vehicle
        vehicle = user_context.get("vehicle", {})
        if param_lower == "vehicleid" and "id" in vehicle:
            return vehicle["id"]
        if param_lower == "licenceplate" and "plate" in vehicle:
            return vehicle["plate"]

        return None

    async def _fix_vehicle_id(
        self,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Optional[str]:
        """Try to fix VehicleId parameter."""
        # From user context
        vehicle = user_context.get("vehicle", {})
        if vehicle.get("id"):
            return vehicle["id"]

        # From parameters (plate -> id)
        plate = None
        for key, value in parameters.items():
            if "plate" in key.lower() and value:
                plate = value
                break

        if plate and self.registry:
            # Could call vehicle lookup API here
            # For now, just return None
            pass

        return None

    async def _fix_person_id(
        self,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Optional[str]:
        """Try to fix PersonId parameter."""
        return user_context.get("person_id")

    async def _fix_datetime(
        self,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Optional[str]:
        """Try to fix datetime parameters."""
        # This would use NLP to parse datetime from text
        # For now, return None - requires user input
        return None

    async def _try_alternatives(
        self,
        alternatives: List[str],
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        context: ToolExecutionContext
    ) -> ExecutionResult:
        """Try alternative tools."""
        result = ExecutionResult(success=False, attempts=[])

        for alt_name in alternatives:
            tool = self.registry.get_tool(alt_name)
            if not tool:
                continue

            # Adapt parameters for alternative tool
            adapted_params = self._adapt_parameters(parameters, tool)

            attempt = await self._execute_single(tool, adapted_params, context)
            result.attempts.append(attempt)

            if attempt.success:
                result.success = True
                result.data = attempt.data
                result.final_tool = alt_name
                result.final_params = adapted_params
                logger.info(f"Alternative tool {alt_name} succeeded!")
                return result

            logger.info(f"Alternative {alt_name} failed: {attempt.error}")

        return result

    def _adapt_parameters(
        self,
        parameters: Dict[str, Any],
        tool
    ) -> Dict[str, Any]:
        """Adapt parameters for a different tool."""
        adapted = {}

        # Get tool's expected parameters
        tool_params = set(tool.parameters.keys())

        # Copy matching parameters
        for key, value in parameters.items():
            if key in tool_params:
                adapted[key] = value
            else:
                # Try case-insensitive match
                for tool_param in tool_params:
                    if key.lower() == tool_param.lower():
                        adapted[tool_param] = value
                        break

        # Common parameter aliases
        aliases = {
            "from": ["FromTime", "StartTime", "from_time"],
            "to": ["ToTime", "EndTime", "to_time"],
            "vehicleId": ["VehicleId", "vehicle_id", "Id"],
            "personId": ["PersonId", "person_id", "DriverId"],
        }

        for canonical, variants in aliases.items():
            if canonical in parameters:
                for variant in variants:
                    if variant in tool_params and variant not in adapted:
                        adapted[variant] = parameters[canonical]

        return adapted

    async def _find_alternatives(
        self,
        failed_tool: str,
        user_query: str,
        user_context: Dict[str, Any]
    ) -> List[str]:
        """Find alternative tools via search."""
        if not self.search_engine:
            return []

        try:
            # Search for similar tools
            matches = await self.search_engine.search(
                query=user_query,
                user_context=user_context,
                top_k=5
            )

            # Filter out the failed tool and low-score matches
            alternatives = []
            for match in matches:
                name = match.get("name") or match.get("operation_id")
                score = match.get("score", 0)
                if name and name != failed_tool and score >= 0.5:
                    alternatives.append(name)

            return alternatives[:self.MAX_ALTERNATIVES]

        except Exception as e:
            logger.error(f"Error finding alternatives: {e}")
            return []

    def _get_user_message(
        self,
        category: ErrorCategory,
        error: Optional[str],
        tool_name: str
    ) -> str:
        """Get user-friendly error message."""
        messages = {
            ErrorCategory.PARAMETER_ERROR: (
                "Nedostaju mi neki podaci za ovu operaciju. "
                "Možete li pojasniti što točno trebate?"
            ),
            ErrorCategory.AUTH_ERROR: (
                "Nemate pristup ovoj funkciji. "
                "Molimo kontaktirajte administratora za pomoć."
            ),
            ErrorCategory.NOT_FOUND: (
                "Nisam pronašao tražene podatke. "
                "Možete li provjeriti jeste li unijeli točne informacije?"
            ),
            ErrorCategory.RATE_LIMIT: (
                "Sustav je trenutno preopterećen. "
                "Molimo pokušajte ponovno za nekoliko sekundi."
            ),
            ErrorCategory.SERVER_ERROR: (
                "Došlo je do tehničke greške na serveru. "
                "Molimo pokušajte ponovno kasnije."
            ),
            ErrorCategory.VALIDATION_ERROR: (
                "Format podataka nije ispravan. "
                "Možete li provjeriti unesene vrijednosti?"
            ),
            ErrorCategory.UNKNOWN: (
                "Došlo je do neočekivane greške. "
                "Kako vam još mogu pomoći?"
            ),
        }

        return messages.get(category, messages[ErrorCategory.UNKNOWN])


# Singleton
_executor_fallback = None


def get_executor_with_fallback(registry=None, executor=None, search_engine=None) -> ExecutorWithFallback:
    """Get singleton instance."""
    global _executor_fallback
    if _executor_fallback is None:
        if registry is None or executor is None:
            raise ValueError("Registry and executor required for first initialization")
        _executor_fallback = ExecutorWithFallback(registry, executor, search_engine)
    return _executor_fallback

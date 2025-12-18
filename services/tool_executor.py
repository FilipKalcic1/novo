"""
Tool Executor
Version: 11.0

Dynamic API execution with validation and auto-injection.

CRITICAL FIXES:
1. Auto-injects user_id, tenant_id, person_id
2. Validates parameters against schema
3. Provides detailed error feedback for AI
4. Type coercion for common mismatches
"""

import re
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

from services.api_gateway import APIGateway, HttpMethod, APIResponse

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Parameter validation error with details for AI feedback."""
    
    def __init__(self, message: str, missing: List[str] = None, invalid: Dict[str, str] = None):
        super().__init__(message)
        self.missing = missing or []
        self.invalid = invalid or {}
    
    def ai_feedback(self) -> str:
        """Generate feedback message for AI."""
        parts = [self.args[0]]
        
        if self.missing:
            parts.append(f"Missing required parameters: {', '.join(self.missing)}")
        
        if self.invalid:
            for param, reason in self.invalid.items():
                parts.append(f"Invalid '{param}': {reason}")
        
        return ". ".join(parts)


class ToolExecutor:
    """
    Executes API tools with validation and auto-injection.
    
    Features:
    - Automatic parameter injection (person_id, tenant_id)
    - Schema validation before execution
    - Type coercion (string to int, date parsing)
    - Detailed error messages for AI feedback
    """
    
    # Parameters to auto-inject from user context
    AUTO_INJECT_MAP = {
        "personid": "person_id",
        "assignedtoid": "person_id",
        "driverid": "person_id",
        "tenantid": "tenant_id",
        "createdby": "person_id",
        "modifiedby": "person_id",
        "userid": "person_id",
    }
    
    def __init__(self, gateway: APIGateway, registry):
        """Initialize executor."""
        self.gateway = gateway
        self.registry = registry
    
    async def execute(
        self,
        operation_id: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute tool with validation.
        
        Args:
            operation_id: Tool operation ID
            parameters: Parameters from AI (may be incomplete/wrong)
            user_context: User context for auto-injection
            
        Returns:
            Result dict with success/error and AI feedback
        """
        # 1. Get tool definition
        tool = self.registry.get_tool(operation_id)
        if not tool:
            return self._error_response(
                f"Tool '{operation_id}' not found",
                "TOOL_NOT_FOUND",
                ai_feedback=f"The tool '{operation_id}' does not exist. Check available tools."
            )
        
        logger.info(f"🔧 Executing: {operation_id}")
        
        try:
            # 2. Auto-inject parameters
            params = self._auto_inject(parameters.copy(), tool, user_context)
            
            # 3. Validate and coerce types
            params, validation_warnings = self._validate_and_coerce(params, tool)
            
            if validation_warnings:
                logger.warning(f"Validation warnings: {validation_warnings}")
            
            # 4. Check required parameters
            missing = self._check_required(params, tool)
            if missing:
                return self._error_response(
                    f"Missing required parameters: {', '.join(missing)}",
                    "MISSING_PARAMS",
                    ai_feedback=f"Please provide values for: {', '.join(missing)}"
                )
            
            # 5. Substitute path parameters
            path = self._substitute_path(tool["path"], params)
            
            # 6. Prepare request
            method = tool["method"]
            query_params, body = self._prepare_request(params, tool, method)
            
            # 7. Execute API call
            logger.info(f"📡 API: {method} {path}")
            
            response = await self.gateway.execute(
                method=HttpMethod[method],
                path=path,
                params=query_params,
                body=body,
                tenant_id=user_context.get("tenant_id")
            )
            
            # 8. Format response
            return self._format_response(response, tool)
            
        except ValidationError as e:
            return self._error_response(
                str(e),
                "VALIDATION_ERROR",
                ai_feedback=e.ai_feedback()
            )
        except Exception as e:
            logger.error(f"❌ Execution error: {e}", exc_info=True)
            return self._error_response(
                str(e),
                "EXECUTION_ERROR",
                ai_feedback=f"Execution failed: {e}"
            )
    
    def _auto_inject(
        self,
        params: Dict[str, Any],
        tool: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Auto-inject parameters from user context.
        
        Injects:
        - personId, assignedToId, driverId → from user_context["person_id"]
        - tenantId → from user_context["tenant_id"]
        """
        tool_params = tool.get("parameters", {})
        auto_inject_list = tool.get("auto_inject", [])
        
        # Inject from auto_inject list
        for param_name in auto_inject_list:
            if param_name in params and params[param_name]:
                continue
            
            param_lower = param_name.lower()
            context_key = self.AUTO_INJECT_MAP.get(param_lower)
            
            if context_key and context_key in user_context:
                value = user_context[context_key]
                if value:
                    params[param_name] = value
                    logger.debug(f"Auto-injected: {param_name}")
        
        # Also check tool parameters for injectable params
        for param_name, param_def in tool_params.items():
            if param_name in params and params[param_name]:
                continue
            
            param_lower = param_name.lower()
            context_key = self.AUTO_INJECT_MAP.get(param_lower)
            
            if context_key and context_key in user_context:
                value = user_context[context_key]
                if value:
                    params[param_name] = value
                    logger.debug(f"Auto-injected from schema: {param_name}")
        
        # Special defaults for specific operations
        operation_lower = tool.get("operationId", "").lower()
        
        if "calendar" in operation_lower or "booking" in operation_lower:
            params.setdefault("AssigneeType", 1)
            params.setdefault("EntryType", 0)
        
        return params
    
    def _validate_and_coerce(
        self,
        params: Dict[str, Any],
        tool: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Validate and coerce parameter types.
        
        Returns:
            (coerced_params, warnings)
        """
        tool_params = tool.get("parameters", {})
        warnings = []
        result = {}
        
        for param_name, value in params.items():
            if value is None:
                continue
            
            param_def = tool_params.get(param_name, {})
            expected_type = param_def.get("type", "string")
            param_format = param_def.get("format")
            
            try:
                coerced = self._coerce_type(value, expected_type, param_format)
                result[param_name] = coerced
            except (ValueError, TypeError) as e:
                warnings.append(f"Could not coerce {param_name}: {e}")
                result[param_name] = value
        
        return result, warnings
    
    def _coerce_type(self, value: Any, expected_type: str, param_format: Optional[str]) -> Any:
        """Coerce value to expected type."""
        if value is None:
            return None
        
        if expected_type == "integer":
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return int(float(value))
            return int(value)
        
        if expected_type == "number":
            if isinstance(value, (int, float)):
                return float(value)
            return float(value)
        
        if expected_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "da")
            return bool(value)
        
        if expected_type == "string":
            if param_format == "date-time":
                return self._parse_datetime(value)
            if param_format == "date":
                return self._parse_date(value)
            return str(value)
        
        if expected_type == "array":
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except:
                    return [value]
            return [value]
        
        return value
    
    def _parse_datetime(self, value: Any) -> str:
        """Parse datetime to ISO format."""
        if isinstance(value, datetime):
            return value.isoformat()
        
        if isinstance(value, str):
            # Already ISO format
            if "T" in value:
                return value
            
            # Try common formats
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.isoformat()
                except ValueError:
                    continue
            
            return value
        
        return str(value)
    
    def _parse_date(self, value: Any) -> str:
        """Parse date to YYYY-MM-DD format."""
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        
        if isinstance(value, str):
            if len(value) == 10 and "-" in value:
                return value
            
            for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue
        
        return str(value)
    
    def _check_required(self, params: Dict[str, Any], tool: Dict[str, Any]) -> List[str]:
        """Check for missing required parameters."""
        required = tool.get("required", [])
        tool_params = tool.get("parameters", {})
        
        # Also check individual parameter required flags
        for param_name, param_def in tool_params.items():
            if param_def.get("required") and param_name not in required:
                required.append(param_name)
        
        missing = []
        for param in required:
            if param not in params or params[param] is None:
                missing.append(param)
        
        return missing
    
    def _substitute_path(self, path: str, params: Dict[str, Any]) -> str:
        """Substitute {placeholders} in path."""
        placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", path)
        
        for placeholder in placeholders:
            # Find matching param (case-insensitive)
            for key in list(params.keys()):
                if key.lower() == placeholder.lower():
                    path = path.replace(f"{{{placeholder}}}", str(params[key]))
                    del params[key]
                    break
        
        return path
    
    def _prepare_request(
        self,
        params: Dict[str, Any],
        tool: Dict[str, Any],
        method: str
    ) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Prepare query params and body."""
        if method in ("GET", "DELETE"):
            return params if params else None, None
        
        # For POST/PUT/PATCH: separate query from body
        tool_params = tool.get("parameters", {})
        query = {}
        body = {}
        
        for param_name, value in params.items():
            if value is None:
                continue
            
            param_def = tool_params.get(param_name, {})
            location = param_def.get("in", "body")
            
            if location == "query":
                query[param_name] = value
            else:
                body[param_name] = value
        
        return query if query else None, body if body else None
    
    def _format_response(self, response: APIResponse, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Format API response."""
        if not response.success:
            return self._error_response(
                response.error_message or "API call failed",
                response.error_code or "API_ERROR",
                status_code=response.status_code,
                ai_feedback=self._generate_api_error_feedback(response, tool)
            )
        
        operation_id = tool.get("operationId", "unknown")
        method = tool.get("method", "GET")
        data = response.data
        
        if method == "GET":
            return self._format_get(data, operation_id)
        elif method in ("POST", "PUT", "PATCH"):
            return self._format_mutation(data, operation_id)
        elif method == "DELETE":
            return {"success": True, "deleted": True, "operation": operation_id}
        
        return {"success": True, "data": data, "operation": operation_id}
    
    def _format_get(self, data: Any, operation_id: str) -> Dict[str, Any]:
        """Format GET response."""
        if isinstance(data, list):
            return {
                "success": True,
                "operation": operation_id,
                "count": len(data),
                "items": data[:100]
            }
        
        if isinstance(data, dict):
            # Extract items from common wrapper patterns
            items = (
                data.get("Data") or
                data.get("Items") or
                data.get("items") or
                data.get("results") or
                data.get("value")
            )
            
            if isinstance(items, list):
                return {
                    "success": True,
                    "operation": operation_id,
                    "count": len(items),
                    "total": data.get("Count") or data.get("TotalCount") or len(items),
                    "items": items[:100]
                }
            
            return {"success": True, "operation": operation_id, "data": data}
        
        return {"success": True, "operation": operation_id, "data": data}
    
    def _format_mutation(self, data: Any, operation_id: str) -> Dict[str, Any]:
        """Format POST/PUT/PATCH response."""
        result_id = None
        if isinstance(data, dict):
            result_id = data.get("Id") or data.get("id") or data.get("ID")
        
        return {
            "success": True,
            "operation": operation_id,
            "created_id": result_id,
            "data": data
        }
    
    def _error_response(
        self,
        error: str,
        error_code: str,
        status_code: int = None,
        ai_feedback: str = None
    ) -> Dict[str, Any]:
        """Create error response with AI feedback."""
        return {
            "success": False,
            "error": error,
            "error_code": error_code,
            "status_code": status_code,
            "ai_feedback": ai_feedback or error
        }
    
    def _generate_api_error_feedback(self, response: APIResponse, tool: Dict[str, Any]) -> str:
        """Generate helpful AI feedback from API error."""
        status = response.status_code
        error = response.error_message or "Unknown error"
        
        if status == 400:
            return f"Bad request: {error}. Check parameter values and types."
        elif status == 401:
            return "Authentication failed. The token may be invalid."
        elif status == 403:
            return f"Access denied: {error}. The user may not have permission."
        elif status == 404:
            return f"Not found: {error}. The resource may not exist."
        elif status == 422:
            return f"Validation error: {error}. Check the parameter values."
        elif status >= 500:
            return f"Server error: {error}. Try again later."
        
        return error

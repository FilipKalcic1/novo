"""
Schema Sanitizer - OpenAI JSON Schema Validation & Auto-Fix
Version: 2.0

FIX #13: Ensures all tool schemas are valid before sending to OpenAI.

Key Fixes:
1. Arrays without items → Auto-add {type: "object"}
2. Invalid types → Map to valid JSON Schema types
3. Missing required → Ensure empty array if none
4. Deep validation → Catch all OpenAI validation errors

Domain-agnostic. NO business logic.
"""

import logging
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.tool_contracts import UnifiedToolDefinition, ParameterDefinition, DependencySource

logger = logging.getLogger(__name__)


class SchemaSanitizer:
    """
    Sanitizes tool schemas for OpenAI compatibility.

    OpenAI's JSON Schema validation is STRICT:
    - Arrays MUST have "items"
    - Types must be valid JSON Schema types
    - Required must be array (not missing)
    """

    # Valid JSON Schema types per OpenAI spec
    VALID_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}

    @staticmethod
    def sanitize_tool_schema(tool: 'UnifiedToolDefinition') -> Dict[str, Any]:
        """
        Convert tool to OpenAI function format with STRICT validation.

        Args:
            tool: UnifiedToolDefinition instance

        Returns:
            Valid OpenAI function schema

        Raises:
            ValueError: If tool cannot be sanitized
        """
        from services.tool_contracts import DependencySource

        visible_params = {}
        required = []

        for param_name, param_def in tool.parameters.items():
            # Skip context params (invisible to LLM)
            if param_def.dependency_source == DependencySource.FROM_CONTEXT:
                continue

            # Build sanitized schema
            param_schema = SchemaSanitizer._build_param_schema(param_def, param_name)

            visible_params[param_name] = param_schema

            if param_def.required:
                required.append(param_name)

        # Build OpenAI function schema
        function_schema = {
            "type": "function",
            "function": {
                "name": tool.operation_id,
                "description": tool.description[:1024] if tool.description else tool.operation_id,
                "parameters": {
                    "type": "object",
                    "properties": visible_params,
                    "required": required if required else []  # MUST be array (empty if none)
                }
            }
        }

        logger.debug(
            f"Sanitized schema for {tool.operation_id}: "
            f"{len(visible_params)} params, {len(required)} required"
        )

        return function_schema

    @staticmethod
    def _build_param_schema(param_def: 'ParameterDefinition', param_name: str) -> Dict[str, Any]:
        """
        Build JSON Schema for single parameter with validation.

        Args:
            param_def: Parameter definition
            param_name: Parameter name (for logging)

        Returns:
            Valid JSON Schema dict
        """
        # Validate and normalize type
        param_type = param_def.param_type.lower() if param_def.param_type else "string"

        if param_type not in SchemaSanitizer.VALID_JSON_TYPES:
            logger.warning(
                f"Invalid type '{param_type}' for parameter '{param_name}'. "
                f"Defaulting to 'string'. Valid types: {SchemaSanitizer.VALID_JSON_TYPES}"
            )
            param_type = "string"

        # Build base schema
        schema = {
            "type": param_type,
            "description": param_def.description or param_name
        }

        # CRITICAL FIX: Arrays MUST have items property
        if param_type == "array":
            if param_def.items_type and param_def.items_type in SchemaSanitizer.VALID_JSON_TYPES:
                schema["items"] = {"type": param_def.items_type}
            else:
                # DEFAULT: Most flexible type for unknown arrays
                schema["items"] = {"type": "object"}
                logger.debug(
                    f"Array parameter '{param_name}' missing or invalid items_type. "
                    f"Defaulting to items: {{type: 'object'}}"
                )

        # Enum constraint
        if param_def.enum_values:
            schema["enum"] = param_def.enum_values

        # Format hints (for dates, etc.)
        if param_def.format:
            if param_def.format == "date-time":
                schema["description"] += " (Format: ISO 8601 YYYY-MM-DDTHH:MM:SS)"
            elif param_def.format == "date":
                schema["description"] += " (Format: YYYY-MM-DD)"
            elif param_def.format == "uuid":
                schema["description"] += " (Format: UUID)"
            elif param_def.format == "email":
                schema["description"] += " (Format: email@example.com)"

        # Default value (optional hint)
        if param_def.default_value is not None:
            # Note: OpenAI doesn't use "default" in schema, but we can mention it in description
            schema["description"] += f" (Default: {param_def.default_value})"

        return schema

    @staticmethod
    def validate_openai_schema(schema: Dict[str, Any]) -> bool:
        """
        Validate that schema conforms to OpenAI requirements.

        Args:
            schema: OpenAI function schema

        Returns:
            True if valid

        Raises:
            ValueError: If schema is invalid
        """
        # Check top-level structure
        if "type" not in schema or schema["type"] != "function":
            raise ValueError("Schema must have type: 'function'")

        if "function" not in schema:
            raise ValueError("Schema must have 'function' key")

        func = schema["function"]

        # Check function metadata
        if "name" not in func or not func["name"]:
            raise ValueError("Function must have non-empty 'name'")

        if "description" not in func:
            raise ValueError("Function must have 'description'")

        # Check parameters
        if "parameters" not in func:
            raise ValueError("Function must have 'parameters'")

        params = func["parameters"]

        if "type" not in params or params["type"] != "object":
            raise ValueError("Parameters must have type: 'object'")

        if "properties" not in params:
            raise ValueError("Parameters must have 'properties'")

        # Validate each property
        for prop_name, prop_schema in params.get("properties", {}).items():
            SchemaSanitizer._validate_property_schema(prop_name, prop_schema)

        # Validate required array
        if "required" in params and not isinstance(params["required"], list):
            raise ValueError("Parameters 'required' must be array")

        return True

    @staticmethod
    def _validate_property_schema(prop_name: str, prop_schema: Dict[str, Any]) -> None:
        """
        Validate single property schema.

        Args:
            prop_name: Property name
            prop_schema: Property schema dict

        Raises:
            ValueError: If property schema is invalid
        """
        if "type" not in prop_schema:
            raise ValueError(f"Property '{prop_name}' missing 'type'")

        prop_type = prop_schema["type"]

        if prop_type not in SchemaSanitizer.VALID_JSON_TYPES:
            raise ValueError(
                f"Property '{prop_name}' has invalid type '{prop_type}'. "
                f"Valid types: {SchemaSanitizer.VALID_JSON_TYPES}"
            )

        # CRITICAL: Arrays must have items
        if prop_type == "array":
            if "items" not in prop_schema:
                raise ValueError(
                    f"Array property '{prop_name}' missing 'items'. "
                    f"OpenAI requires all arrays to have items definition."
                )

            items_schema = prop_schema["items"]
            if "type" not in items_schema:
                raise ValueError(
                    f"Array property '{prop_name}' items missing 'type'"
                )

        # Description is recommended
        if "description" not in prop_schema:
            logger.warning(f"Property '{prop_name}' missing description")

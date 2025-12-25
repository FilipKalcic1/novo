"""
Schema Validator
Version: 10.0

Validates and fixes OpenAPI schemas for OpenAI compatibility.
NO DEPENDENCIES on other services.

CRITICAL: Fixes "array schema missing items" error.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Set
from copy import deepcopy

logger = logging.getLogger(__name__)


class SchemaValidator:
    """
    Validates and fixes schemas for OpenAI function calling.
    
    OpenAI Requirements:
    1. Arrays MUST have 'items' property
    2. No 'nullable' - not supported
    3. Types must be: string, number, integer, boolean, array, object
    """
    
    SUPPORTED_TYPES: Set[str] = {"string", "number", "integer", "boolean", "array", "object"}
    SUPPORTED_FORMATS: Set[str] = {"date-time", "date", "time", "email", "uri", "uuid"}
    UNSUPPORTED_PROPS: Set[str] = {
        "nullable", "readOnly", "writeOnly", "deprecated",
        "xml", "externalDocs", "example", "examples",
        "$ref", "allOf", "oneOf", "anyOf", "not"
    }
    
    @classmethod
    def validate_and_fix(
        cls,
        schema: Dict[str, Any],
        path: str = "root",
        depth: int = 0
    ) -> Dict[str, Any]:
        """
        Recursively validate and fix schema.
        
        Args:
            schema: Schema to validate
            path: Current path for logging
            depth: Recursion depth
            
        Returns:
            Fixed schema
        """
        if depth > 20:
            logger.warning(f"Max depth at {path}")
            return {"type": "string", "description": f"Complex type at {path}"}
        
        if not schema or not isinstance(schema, dict):
            return {"type": "string"}
        
        schema = deepcopy(schema)
        
        # Remove unsupported properties
        for prop in cls.UNSUPPORTED_PROPS:
            schema.pop(prop, None)
        
        fixed = {}
        schema_type = schema.get("type")
        
        # === ARRAY ===
        if schema_type == "array":
            fixed["type"] = "array"
            
            # CRITICAL: Arrays MUST have items
            if "items" in schema and schema["items"]:
                fixed["items"] = cls.validate_and_fix(
                    schema["items"],
                    f"{path}.items",
                    depth + 1
                )
            else:
                logger.warning(f"Array missing 'items' at {path}")
                fixed["items"] = {"type": "string"}
            
            if "minItems" in schema:
                fixed["minItems"] = int(schema["minItems"])
            if "maxItems" in schema:
                fixed["maxItems"] = int(schema["maxItems"])
        
        # === OBJECT ===
        elif schema_type == "object" or "properties" in schema:
            fixed["type"] = "object"
            
            if "properties" in schema and isinstance(schema["properties"], dict):
                fixed["properties"] = {}
                for prop_name, prop_schema in schema["properties"].items():
                    if isinstance(prop_schema, dict):
                        fixed["properties"][prop_name] = cls.validate_and_fix(
                            prop_schema,
                            f"{path}.{prop_name}",
                            depth + 1
                        )
                    else:
                        fixed["properties"][prop_name] = {"type": "string"}
            
            if "required" in schema and isinstance(schema["required"], list):
                valid_props = set(fixed.get("properties", {}).keys())
                fixed["required"] = [
                    str(r) for r in schema["required"]
                    if isinstance(r, str) and r in valid_props
                ]
            
            if "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    fixed["additionalProperties"] = False
                elif isinstance(ap, dict):
                    fixed["additionalProperties"] = cls.validate_and_fix(
                        ap,
                        f"{path}.additionalProperties",
                        depth + 1
                    )
        
        # === PRIMITIVES ===
        elif schema_type in ("string", "number", "integer", "boolean"):
            fixed["type"] = schema_type
            
            if "enum" in schema and isinstance(schema["enum"], list):
                if schema_type == "string":
                    fixed["enum"] = [str(e) for e in schema["enum"]]
                else:
                    fixed["enum"] = list(schema["enum"])
            
            if "format" in schema and schema["format"] in cls.SUPPORTED_FORMATS:
                fixed["format"] = schema["format"]
            
            # Numeric constraints
            if schema_type in ("number", "integer"):
                for c in ["minimum", "maximum"]:
                    if c in schema:
                        try:
                            fixed[c] = float(schema[c]) if schema_type == "number" else int(schema[c])
                        except (ValueError, TypeError):
                            pass
            
            # String constraints
            if schema_type == "string":
                for c in ["minLength", "maxLength"]:
                    if c in schema:
                        try:
                            fixed[c] = int(schema[c])
                        except (ValueError, TypeError):
                            pass
        
        # === INFER TYPE ===
        elif schema_type is None:
            if "properties" in schema:
                return cls.validate_and_fix({**schema, "type": "object"}, path, depth)
            elif "items" in schema:
                return cls.validate_and_fix({**schema, "type": "array"}, path, depth)
            elif "enum" in schema:
                fixed["type"] = "string"
                fixed["enum"] = [str(e) for e in schema["enum"]]
            else:
                fixed["type"] = "string"
        
        # === UNKNOWN TYPE ===
        else:
            logger.warning(f"Unknown type '{schema_type}' at {path}")
            fixed["type"] = "string"
        
        # Copy safe properties
        if "description" in schema:
            fixed["description"] = str(schema["description"])[:500]
        
        if "default" in schema:
            fixed["default"] = schema["default"]
        
        return fixed
    
    @classmethod
    def create_openai_function(
        cls,
        name: str,
        description: str,
        parameters: Dict[str, Dict[str, Any]],
        required: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Create OpenAI function definition.
        
        Args:
            name: Function name
            description: Function description
            parameters: Parameter schemas
            required: Required parameters
            
        Returns:
            OpenAI function definition
        """
        validated_props = {}
        for param_name, param_schema in parameters.items():
            validated_props[param_name] = cls.validate_and_fix(param_schema, param_name)
        
        valid_required = []
        if required:
            valid_required = [r for r in required if r in validated_props]
        
        func_def = {
            "type": "function",
            "function": {
                "name": cls._sanitize_name(name),
                "description": description[:1024] if description else name,
                "parameters": {
                    "type": "object",
                    "properties": validated_props
                }
            }
        }
        
        if valid_required:
            func_def["function"]["parameters"]["required"] = valid_required
        
        return func_def
    
    @classmethod
    def _sanitize_name(cls, name: str) -> str:
        """Sanitize function name."""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        if sanitized and sanitized[0].isdigit():
            sanitized = "_" + sanitized
        return sanitized[:64]
    
    @classmethod
    def validate_function_schema(cls, schema: Dict[str, Any]) -> List[str]:
        """
        Validate complete function schema.
        
        Returns list of errors (empty if valid).
        """
        errors = []
        
        if not isinstance(schema, dict):
            return ["Schema must be a dictionary"]
        
        if schema.get("type") != "function":
            errors.append("Schema type must be 'function'")
        
        func = schema.get("function", {})
        
        if not func.get("name"):
            errors.append("Function must have a name")
        
        params = func.get("parameters", {})
        
        if params.get("type") != "object":
            errors.append("Parameters type must be 'object'")
        
        # Check arrays have items
        errors.extend(cls._check_arrays(params, "parameters"))
        
        return errors
    
    @classmethod
    def _check_arrays(cls, schema: Dict, path: str) -> List[str]:
        """Check all arrays have items."""
        errors = []
        
        if not isinstance(schema, dict):
            return errors
        
        if schema.get("type") == "array" and "items" not in schema:
            errors.append(f"Array at {path} missing 'items'")
        
        for prop_name, prop_schema in schema.get("properties", {}).items():
            errors.extend(cls._check_arrays(prop_schema, f"{path}.{prop_name}"))
        
        if "items" in schema:
            errors.extend(cls._check_arrays(schema["items"], f"{path}.items"))
        
        return errors

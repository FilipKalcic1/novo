"""
Tests for SchemaValidator
Version: 11.0

Tests OpenAPI schema validation and fixing.
"""

import pytest
from services.schema_validator import SchemaValidator


class TestSchemaValidator:
    """Test SchemaValidator class."""
    
    @pytest.fixture
    def validator(self):
        return SchemaValidator()
    
    # ========================================================================
    # ARRAY VALIDATION
    # ========================================================================
    
    def test_fix_array_missing_items(self, validator):
        """Arrays without items should get default items."""
        schema = {
            "type": "array"
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "items" in result
        assert result["items"]["type"] == "string"
    
    def test_preserve_existing_items(self, validator):
        """Existing items definition should be preserved."""
        schema = {
            "type": "array",
            "items": {"type": "integer"}
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert result["items"]["type"] == "integer"
    
    def test_nested_array_items(self, validator):
        """Nested arrays should all have items."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array"},
                "nested": {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array"}
                    }
                }
            }
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "items" in result["properties"]["tags"]
        assert "items" in result["properties"]["nested"]["properties"]["items"]
    
    # ========================================================================
    # UNSUPPORTED PROPERTIES
    # ========================================================================
    
    def test_remove_nullable(self, validator):
        """nullable property should be removed."""
        schema = {
            "type": "string",
            "nullable": True
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "nullable" not in result
    
    def test_remove_ref(self, validator):
        """$ref should be removed and replaced with object type."""
        schema = {
            "$ref": "#/definitions/SomeType"
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "$ref" not in result
        assert result.get("type") == "object"
    
    def test_remove_allOf(self, validator):
        """allOf should be removed."""
        schema = {
            "allOf": [
                {"type": "object"},
                {"properties": {"name": {"type": "string"}}}
            ]
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "allOf" not in result
    
    def test_remove_oneOf(self, validator):
        """oneOf should be removed."""
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"}
            ]
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "oneOf" not in result
    
    def test_remove_anyOf(self, validator):
        """anyOf should be removed."""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "null"}
            ]
        }
        result = validator.validate_and_fix(schema.copy())
        
        assert "anyOf" not in result
    
    # ========================================================================
    # OPENAI FUNCTION CREATION
    # ========================================================================
    
    def test_create_openai_function_basic(self, validator):
        """Create basic OpenAI function definition."""
        tool = {
            "operationId": "get_Vehicles",
            "description": "Get list of vehicles",
            "parameters": {
                "status": {
                    "type": "string",
                    "description": "Vehicle status"
                }
            },
            "required": []
        }
        
        result = validator.create_openai_function(tool)
        
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_Vehicles"
        assert result["function"]["description"] == "Get list of vehicles"
        assert "parameters" in result["function"]
    
    def test_create_openai_function_with_required(self, validator):
        """Required parameters should be included."""
        tool = {
            "operationId": "create_Booking",
            "description": "Create booking",
            "parameters": {
                "vehicleId": {"type": "string"},
                "fromTime": {"type": "string"}
            },
            "required": ["vehicleId", "fromTime"]
        }
        
        result = validator.create_openai_function(tool)
        params = result["function"]["parameters"]
        
        assert "required" in params
        assert "vehicleId" in params["required"]
        assert "fromTime" in params["required"]
    
    def test_create_openai_function_fixes_arrays(self, validator):
        """Arrays in function params should be fixed."""
        tool = {
            "operationId": "filter_Vehicles",
            "description": "Filter vehicles",
            "parameters": {
                "statuses": {"type": "array"}  # Missing items
            },
            "required": []
        }
        
        result = validator.create_openai_function(tool)
        params = result["function"]["parameters"]
        
        assert "items" in params["properties"]["statuses"]
    
    # ========================================================================
    # EDGE CASES
    # ========================================================================
    
    def test_empty_schema(self, validator):
        """Empty schema should be handled."""
        result = validator.validate_and_fix({})
        assert result == {}
    
    def test_none_schema(self, validator):
        """None schema should be handled."""
        result = validator.validate_and_fix(None)
        assert result is None
    
    def test_max_depth_protection(self, validator):
        """Deep nesting should be limited."""
        # Create deeply nested schema
        schema = {"type": "object", "properties": {}}
        current = schema["properties"]
        for i in range(20):
            current[f"level_{i}"] = {
                "type": "object",
                "properties": {}
            }
            current = current[f"level_{i}"]["properties"]
        
        # Should not crash
        result = validator.validate_and_fix(schema)
        assert result is not None
    
    def test_circular_reference_protection(self, validator):
        """Circular references should be handled."""
        schema = {"type": "object", "properties": {}}
        schema["properties"]["self"] = schema  # Circular!
        
        # Should not crash (depth limit should catch this)
        try:
            result = validator.validate_and_fix(schema)
            # If it doesn't crash, that's fine
            assert True
        except RecursionError:
            pytest.fail("Circular reference caused RecursionError")

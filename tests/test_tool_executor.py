"""
Tests for ToolExecutor
Version: 11.0

Tests tool execution, validation, and auto-injection.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.tool_executor import ToolExecutor, ValidationError


class TestToolExecutor:
    """Test ToolExecutor class."""
    
    @pytest.fixture
    def mock_gateway(self):
        """Mock API gateway."""
        gateway = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = {"Id": "result-123"}
        response.status_code = 200
        response.error_message = None
        response.error_code = None
        gateway.execute = AsyncMock(return_value=response)
        return gateway
    
    @pytest.fixture
    def mock_registry(self, sample_tool):
        """Mock tool registry."""
        registry = MagicMock()
        registry.get_tool = MagicMock(return_value=sample_tool)
        return registry
    
    @pytest.fixture
    def executor(self, mock_gateway, mock_registry):
        """Create executor with mocks."""
        return ToolExecutor(mock_gateway, mock_registry)
    
    # ========================================================================
    # BASIC EXECUTION
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_execute_success(self, executor, sample_user_context):
        """Successful tool execution."""
        result = await executor.execute(
            "get_Vehicles",
            {"status": "Available"},
            sample_user_context
        )
        
        assert result["success"] is True
        assert "data" in result or "items" in result
    
    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self, executor, sample_user_context):
        """Tool not found should return error."""
        executor.registry.get_tool = MagicMock(return_value=None)
        
        result = await executor.execute(
            "nonexistent_tool",
            {},
            sample_user_context
        )
        
        assert result["success"] is False
        assert result["error_code"] == "TOOL_NOT_FOUND"
        assert "ai_feedback" in result
    
    # ========================================================================
    # AUTO-INJECTION
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_auto_inject_person_id(self, executor, sample_user_context, sample_tool_with_injection):
        """PersonId should be auto-injected."""
        executor.registry.get_tool = MagicMock(return_value=sample_tool_with_injection)
        
        # Call with empty params - should inject AssignedToId
        result = await executor.execute(
            "get_VehicleCalendar",
            {"FromTime": "2024-01-01T09:00:00", "ToTime": "2024-01-01T17:00:00"},
            sample_user_context
        )
        
        # Check that gateway was called with injected param
        call_args = executor.gateway.execute.call_args
        assert call_args is not None
    
    @pytest.mark.asyncio
    async def test_no_override_provided_params(self, executor, sample_user_context, sample_tool_with_injection):
        """Provided params should not be overridden."""
        executor.registry.get_tool = MagicMock(return_value=sample_tool_with_injection)
        
        custom_id = "custom-person-id"
        await executor.execute(
            "get_VehicleCalendar",
            {
                "FromTime": "2024-01-01T09:00:00",
                "ToTime": "2024-01-01T17:00:00",
                "AssignedToId": custom_id
            },
            sample_user_context
        )
        
        # Should keep custom_id, not override with context
        call_args = executor.gateway.execute.call_args
        assert call_args is not None
    
    # ========================================================================
    # TYPE COERCION
    # ========================================================================
    
    def test_coerce_string_to_int(self, executor):
        """String should be coerced to integer."""
        result = executor._coerce_type("42", "integer", None)
        assert result == 42
        assert isinstance(result, int)
    
    def test_coerce_string_to_bool_true(self, executor):
        """String should be coerced to boolean."""
        assert executor._coerce_type("true", "boolean", None) is True
        assert executor._coerce_type("yes", "boolean", None) is True
        assert executor._coerce_type("da", "boolean", None) is True  # Croatian
        assert executor._coerce_type("1", "boolean", None) is True
    
    def test_coerce_string_to_bool_false(self, executor):
        """False strings should be coerced correctly."""
        assert executor._coerce_type("false", "boolean", None) is False
        assert executor._coerce_type("no", "boolean", None) is False
        assert executor._coerce_type("0", "boolean", None) is False
    
    def test_coerce_datetime(self, executor):
        """Datetime strings should be normalized."""
        result = executor._coerce_type("2024-01-15", "string", "date-time")
        assert "2024-01-15" in result
    
    def test_coerce_date(self, executor):
        """Date strings should be normalized."""
        result = executor._coerce_type("15.01.2024", "string", "date")
        assert result == "2024-01-15"
    
    def test_coerce_array_from_string(self, executor):
        """JSON string should be coerced to array."""
        result = executor._coerce_type('["a", "b"]', "array", None)
        assert result == ["a", "b"]
    
    def test_coerce_array_single_value(self, executor):
        """Single value should become array."""
        result = executor._coerce_type("value", "array", None)
        assert result == ["value"]
    
    # ========================================================================
    # VALIDATION
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_missing_required_params(self, executor, sample_user_context, sample_tool_with_injection):
        """Missing required params should return error with AI feedback."""
        executor.registry.get_tool = MagicMock(return_value=sample_tool_with_injection)
        
        result = await executor.execute(
            "get_VehicleCalendar",
            {},  # Missing FromTime and ToTime
            sample_user_context
        )
        
        assert result["success"] is False
        assert result["error_code"] == "MISSING_PARAMS"
        assert "ai_feedback" in result
        assert "FromTime" in result["ai_feedback"] or "ToTime" in result["ai_feedback"]
    
    # ========================================================================
    # PATH SUBSTITUTION
    # ========================================================================
    
    def test_path_substitution(self, executor):
        """Path parameters should be substituted."""
        params = {"vehicleId": "vehicle-123", "other": "value"}
        path = "/api/vehicles/{vehicleId}/details"
        
        result = executor._substitute_path(path, params)
        
        assert result == "/api/vehicles/vehicle-123/details"
        assert "vehicleId" not in params  # Should be removed
        assert "other" in params  # Should remain
    
    def test_path_substitution_case_insensitive(self, executor):
        """Path substitution should be case-insensitive."""
        params = {"VehicleId": "vehicle-123"}
        path = "/api/vehicles/{vehicleid}/details"
        
        result = executor._substitute_path(path, params)
        
        assert "vehicle-123" in result
    
    # ========================================================================
    # ERROR HANDLING
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_api_error_returns_feedback(self, executor, sample_user_context):
        """API errors should include AI feedback."""
        error_response = MagicMock()
        error_response.success = False
        error_response.status_code = 400
        error_response.error_message = "Invalid parameters"
        error_response.error_code = "BAD_REQUEST"
        executor.gateway.execute = AsyncMock(return_value=error_response)
        
        result = await executor.execute(
            "get_Vehicles",
            {},
            sample_user_context
        )
        
        assert result["success"] is False
        assert "ai_feedback" in result
        assert "parameter" in result["ai_feedback"].lower()
    
    @pytest.mark.asyncio
    async def test_403_error_feedback(self, executor, sample_user_context):
        """403 errors should suggest permission issue."""
        error_response = MagicMock()
        error_response.success = False
        error_response.status_code = 403
        error_response.error_message = "Forbidden"
        error_response.error_code = "FORBIDDEN"
        executor.gateway.execute = AsyncMock(return_value=error_response)
        
        result = await executor.execute(
            "get_Vehicles",
            {},
            sample_user_context
        )
        
        assert "permission" in result["ai_feedback"].lower()
    
    @pytest.mark.asyncio
    async def test_404_error_feedback(self, executor, sample_user_context):
        """404 errors should suggest resource not found."""
        error_response = MagicMock()
        error_response.success = False
        error_response.status_code = 404
        error_response.error_message = "Not found"
        error_response.error_code = "NOT_FOUND"
        executor.gateway.execute = AsyncMock(return_value=error_response)
        
        result = await executor.execute(
            "get_Vehicles",
            {},
            sample_user_context
        )
        
        assert "not found" in result["ai_feedback"].lower() or "not exist" in result["ai_feedback"].lower()

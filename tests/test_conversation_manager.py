"""
Tests for ConversationManager
Version: 11.0

Tests conversation state management and Redis persistence.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from services.conversation_manager import ConversationManager, ConversationState


class TestConversationManager:
    """Test ConversationManager class."""
    
    @pytest.fixture
    def manager(self, mock_redis):
        """Create manager with mock Redis."""
        return ConversationManager("+385991234567", mock_redis)
    
    @pytest.fixture
    async def loaded_manager(self, mock_redis):
        """Create and load manager."""
        manager = ConversationManager("+385991234567", mock_redis)
        await manager.load()
        return manager
    
    # ========================================================================
    # STATE MANAGEMENT
    # ========================================================================
    
    def test_initial_state_is_idle(self, manager):
        """Initial state should be IDLE."""
        assert manager.get_state() == ConversationState.IDLE
    
    def test_is_in_flow_initial(self, manager):
        """Should not be in flow initially."""
        assert manager.is_in_flow() is False
    
    @pytest.mark.asyncio
    async def test_start_flow(self, loaded_manager):
        """Starting flow should change state."""
        await loaded_manager.start_flow(
            "booking",
            tool="post_VehicleCalendar",
            required_params=["FromTime", "ToTime"]
        )
        
        assert loaded_manager.get_state() == ConversationState.GATHERING_PARAMS
        assert loaded_manager.get_current_flow() == "booking"
        assert loaded_manager.get_current_tool() == "post_VehicleCalendar"
        assert loaded_manager.is_in_flow() is True
    
    @pytest.mark.asyncio
    async def test_add_parameters(self, loaded_manager):
        """Parameters should be accumulated."""
        await loaded_manager.start_flow("test", required_params=["a", "b"])
        
        await loaded_manager.add_parameters({"a": "value_a"})
        assert loaded_manager.get_parameters()["a"] == "value_a"
        assert "a" not in loaded_manager.get_missing_params()
        assert "b" in loaded_manager.get_missing_params()
        
        await loaded_manager.add_parameters({"b": "value_b"})
        assert loaded_manager.has_all_required_params() is True
    
    @pytest.mark.asyncio
    async def test_set_displayed_items(self, loaded_manager, sample_vehicles):
        """Setting items should change to SELECTING state."""
        await loaded_manager.start_flow("booking")
        await loaded_manager.set_displayed_items(sample_vehicles)
        
        assert loaded_manager.get_state() == ConversationState.SELECTING_ITEM
        assert len(loaded_manager.get_displayed_items()) == 3
    
    @pytest.mark.asyncio
    async def test_select_item(self, loaded_manager, sample_vehicles):
        """Selecting item should store selection."""
        await loaded_manager.start_flow("booking")
        await loaded_manager.set_displayed_items(sample_vehicles)
        await loaded_manager.select_item(sample_vehicles[0])
        
        selected = loaded_manager.get_selected_item()
        assert selected == sample_vehicles[0]
    
    @pytest.mark.asyncio
    async def test_confirmation_flow(self, loaded_manager):
        """Confirmation flow should work."""
        await loaded_manager.start_flow("booking")
        await loaded_manager.request_confirmation("Confirm booking?")
        
        assert loaded_manager.get_state() == ConversationState.CONFIRMING
        
        result = await loaded_manager.confirm()
        assert result is True
        assert loaded_manager.get_state() == ConversationState.EXECUTING
    
    @pytest.mark.asyncio
    async def test_complete_flow(self, loaded_manager):
        """Completing flow should set COMPLETED state."""
        await loaded_manager.start_flow("booking")
        await loaded_manager.complete()
        
        assert loaded_manager.get_state() == ConversationState.COMPLETED
    
    @pytest.mark.asyncio
    async def test_cancel_flow(self, loaded_manager):
        """Cancelling should reset to IDLE."""
        await loaded_manager.start_flow("booking")
        await loaded_manager.cancel()
        
        assert loaded_manager.get_state() == ConversationState.IDLE
        assert loaded_manager.is_in_flow() is False
    
    # ========================================================================
    # PARSING
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_parse_numeric_selection(self, loaded_manager, sample_vehicles):
        """Numeric selection should work."""
        await loaded_manager.set_displayed_items(sample_vehicles)
        
        selected = loaded_manager.parse_item_selection("1")
        assert selected == sample_vehicles[0]
        
        selected = loaded_manager.parse_item_selection("2")
        assert selected == sample_vehicles[1]
    
    @pytest.mark.asyncio
    async def test_parse_name_selection(self, loaded_manager, sample_vehicles):
        """Name-based selection should work."""
        await loaded_manager.set_displayed_items(sample_vehicles)
        
        selected = loaded_manager.parse_item_selection("passat")
        assert selected["Id"] == "vehicle-1"
        
        selected = loaded_manager.parse_item_selection("octavia")
        assert selected["Id"] == "vehicle-2"
    
    @pytest.mark.asyncio
    async def test_parse_plate_selection(self, loaded_manager, sample_vehicles):
        """License plate selection should work."""
        await loaded_manager.set_displayed_items(sample_vehicles)
        
        selected = loaded_manager.parse_item_selection("1234")
        assert selected["Id"] == "vehicle-1"
    
    def test_parse_confirmation_yes(self, manager):
        """Yes confirmations should be recognized."""
        assert manager.parse_confirmation("da") is True
        assert manager.parse_confirmation("Da") is True
        assert manager.parse_confirmation("potvrdi") is True
        assert manager.parse_confirmation("ok") is True
        assert manager.parse_confirmation("može") is True
        assert manager.parse_confirmation("yes") is True
    
    def test_parse_confirmation_no(self, manager):
        """No confirmations should be recognized."""
        assert manager.parse_confirmation("ne") is False
        assert manager.parse_confirmation("Ne") is False
        assert manager.parse_confirmation("odustani") is False
        assert manager.parse_confirmation("cancel") is False
        assert manager.parse_confirmation("stop") is False
    
    def test_parse_confirmation_unknown(self, manager):
        """Unknown responses should return None."""
        assert manager.parse_confirmation("možda") is None
        assert manager.parse_confirmation("ne znam") is None
        assert manager.parse_confirmation("123") is None
    
    # ========================================================================
    # REDIS PERSISTENCE
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_save_to_redis(self, manager, mock_redis):
        """State should be saved to Redis."""
        await manager.start_flow("test")
        
        mock_redis.setex.assert_called()
        call_args = mock_redis.setex.call_args
        assert "conv_state:+385991234567" in call_args[0]
    
    @pytest.mark.asyncio
    async def test_load_from_redis(self, mock_redis):
        """State should be loaded from Redis."""
        saved_state = {
            "state": "gathering",
            "current_flow": "booking",
            "current_tool": "post_VehicleCalendar",
            "parameters": {"FromTime": "2024-01-01"},
            "missing_params": ["ToTime"],
            "displayed_items": [],
            "selected_item": None,
            "confirmation_message": None,
            "started_at": "2024-01-01T00:00:00",
            "last_updated": "2024-01-01T00:00:00"
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(saved_state))
        
        manager = await ConversationManager.load_for_user("+385991234567", mock_redis)
        
        assert manager.get_state() == ConversationState.GATHERING_PARAMS
        assert manager.get_current_flow() == "booking"
        assert manager.get_parameters()["FromTime"] == "2024-01-01"
    
    @pytest.mark.asyncio
    async def test_clear_removes_from_redis(self, loaded_manager, mock_redis):
        """Clear should remove from Redis."""
        await loaded_manager.start_flow("test")
        await loaded_manager.clear()
        
        mock_redis.delete.assert_called()
    
    # ========================================================================
    # TIMEOUT
    # ========================================================================
    
    def test_timeout_not_set(self, manager):
        """No timeout if not started."""
        assert manager.is_timed_out() is False
    
    @pytest.mark.asyncio
    async def test_timeout_recent(self, loaded_manager):
        """Recent flow should not be timed out."""
        await loaded_manager.start_flow("test")
        assert loaded_manager.is_timed_out() is False
    
    # ========================================================================
    # SERIALIZATION
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_to_dict(self, loaded_manager):
        """to_dict should return serializable dict."""
        await loaded_manager.start_flow("test", required_params=["a"])
        await loaded_manager.add_parameters({"a": "value"})
        
        result = loaded_manager.to_dict()
        
        assert isinstance(result, dict)
        assert result["state"] == "gathering"
        assert result["current_flow"] == "test"
        assert result["parameters"]["a"] == "value"
        
        # Should be JSON serializable
        json_str = json.dumps(result)
        assert json_str is not None

"""
Conversation Manager
Version: 11.0

Multi-turn conversation state machine with REDIS PERSISTENCE.

CRITICAL FIX: State is now stored in Redis, survives restarts.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """Conversation states."""
    IDLE = "idle"
    GATHERING_PARAMS = "gathering"
    SELECTING_ITEM = "selecting"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    COMPLETED = "completed"


# Mapiranje sinonima parametara - rješava problem kad AI izvuče "mileage" ali missing_params ima "Value"
KEY_ALIASES = {
    # Mileage variations
    "mileage": ["kilometraža", "km", "Value", "Mileage", "mileage_value"],
    "Value": ["mileage", "kilometraža", "km", "Mileage"],
    "kilometraža": ["mileage", "Value", "km", "Mileage"],
    "Mileage": ["mileage", "Value", "kilometraža", "km"],
    "km": ["mileage", "Value", "kilometraža", "Mileage"],
    # Time variations
    "from": ["FromTime", "start", "od", "from_time"],
    "FromTime": ["from", "start", "od"],
    "to": ["ToTime", "end", "do", "to_time"],
    "ToTime": ["to", "end", "do"],
    # Description variations
    "description": ["Description", "opis", "napomena"],
    "Description": ["description", "opis"],
    # Vehicle variations
    "vehicleId": ["VehicleId", "vehicle_id"],
    "VehicleId": ["vehicleId", "vehicle_id"],
}


@dataclass
class ConversationContext:
    """Context for conversation - serializable."""
    state: str = "idle"
    current_flow: Optional[str] = None
    current_tool: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    missing_params: List[str] = field(default_factory=list)
    displayed_items: List[Dict] = field(default_factory=list)
    selected_item: Optional[Dict] = None
    confirmation_message: Optional[str] = None
    started_at: Optional[str] = None
    last_updated: Optional[str] = None

    # KRITIČNO: tool_outputs za dependency chaining
    # Sprema output vrijednosti iz prethodnih tool poziva
    # Npr: {"VehicleId": "uuid-123", "PersonId": "uuid-456"}
    tool_outputs: Dict[str, Any] = field(default_factory=dict)

    def reset(self):
        """Reset to idle state."""
        self.state = ConversationState.IDLE.value
        self.current_flow = None
        self.current_tool = None
        self.parameters = {}
        self.missing_params = []
        self.displayed_items = []
        self.selected_item = None
        self.confirmation_message = None
        self.tool_outputs = {}  # KRITIČNO: Reset tool outputs


class ConversationManager:
    """
    Manages conversation state with Redis persistence.
    
    Features:
    - Multi-turn flow tracking
    - State survives restarts (Redis storage)
    - Parameter accumulation
    - Item selection handling
    """
    
    FLOW_TIMEOUT_SECONDS = 1800  # 30 minutes
    REDIS_KEY_PREFIX = "conv_state:"
    REDIS_TTL = 86400  # 24 hours
    
    def __init__(self, user_id: str, redis_client=None):
        """
        Initialize for user.
        
        Args:
            user_id: User identifier (phone number)
            redis_client: Redis client for persistence
        """
        self.user_id = user_id
        self.redis = redis_client
        self.context = ConversationContext()
        self._loaded = False
    
    @property
    def _redis_key(self) -> str:
        """Redis key for this user's state."""
        return f"{self.REDIS_KEY_PREFIX}{self.user_id}"
    
    async def load(self) -> None:
        """Load state from Redis."""
        if not self.redis or self._loaded:
            logger.debug(f"Skipping load: redis={bool(self.redis)}, loaded={self._loaded}")
            return

        try:
            data = await self.redis.get(self._redis_key)
            if data:
                state_dict = json.loads(data)
                self.context = ConversationContext(**state_dict)
                # INFO level to ensure we see state loading
                logger.info(
                    f"CONV LOADED: user={self.user_id[-4:]}, state={self.context.state}, "
                    f"flow={self.context.current_flow}, missing={self.context.missing_params}"
                )
            else:
                logger.info(f"CONV LOADED: user={self.user_id[-4:]}, NO STATE IN REDIS (fresh)")
            self._loaded = True
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            self._loaded = True
    
    async def save(self) -> None:
        """Save state to Redis."""
        if not self.redis:
            return
        
        try:
            self.context.last_updated = datetime.utcnow().isoformat()
            data = json.dumps(asdict(self.context))
            await self.redis.setex(self._redis_key, self.REDIS_TTL, data)
            logger.debug(f"Saved state for {self.user_id[-4:]}")
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")
    
    async def clear(self) -> None:
        """Clear state from Redis."""
        if self.redis:
            try:
                await self.redis.delete(self._redis_key)
            except Exception as e:
                logger.warning(f"Failed to clear state: {e}")
        self.context.reset()
    
    def get_state(self) -> ConversationState:
        """Get current state."""
        return ConversationState(self.context.state)
    
    def is_in_flow(self) -> bool:
        """Check if in active flow."""
        return self.context.state != ConversationState.IDLE.value
    
    def get_current_flow(self) -> Optional[str]:
        """Get current flow name."""
        return self.context.current_flow
    
    def get_current_tool(self) -> Optional[str]:
        """Get current tool."""
        return self.context.current_tool
    
    def get_parameters(self) -> Dict[str, Any]:
        """Get collected parameters."""
        return self.context.parameters.copy()
    
    def get_missing_params(self) -> List[str]:
        """Get missing parameters."""
        return self.context.missing_params.copy()
    
    def get_displayed_items(self) -> List[Dict]:
        """Get displayed items."""
        return self.context.displayed_items.copy()
    
    def get_selected_item(self) -> Optional[Dict]:
        """Get selected item."""
        return self.context.selected_item
    
    # === STATE TRANSITIONS ===
    
    async def start_flow(
        self,
        flow_name: str,
        tool: Optional[str] = None,
        required_params: Optional[List[str]] = None
    ):
        """Start new flow."""
        logger.info(f"Starting flow: {flow_name}")
        
        self.context.reset()
        self.context.state = ConversationState.GATHERING_PARAMS.value
        self.context.current_flow = flow_name
        self.context.current_tool = tool
        self.context.missing_params = required_params or []
        self.context.started_at = datetime.utcnow().isoformat()
        self.context.last_updated = datetime.utcnow().isoformat()
        
        await self.save()
    
    async def add_parameters(self, params: Dict[str, Any]):
        """Add collected parameters with alias resolution."""
        for key, value in params.items():
            if value is not None:
                self.context.parameters[key] = value

                # Ukloni iz missing_params - provjeri i aliase
                # Ovo rješava problem kad AI izvuče "mileage" ali missing_params ima "Value"
                keys_to_check = [key] + KEY_ALIASES.get(key, [])
                for k in keys_to_check:
                    if k in self.context.missing_params:
                        self.context.missing_params.remove(k)
                        logger.debug(f"Removed '{k}' from missing_params (matched via '{key}')")

        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()

        logger.debug(f"Parameters: {list(self.context.parameters.keys())}, Still missing: {self.context.missing_params}")
    
    async def set_displayed_items(self, items: List[Dict]):
        """Store displayed items and transition to selecting."""
        self.context.displayed_items = items
        self.context.state = ConversationState.SELECTING_ITEM.value
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()
        
        logger.debug(f"Displayed {len(items)} items")
    
    async def select_item(self, item: Dict):
        """Record selected item."""
        self.context.selected_item = item
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()
        
        logger.info(f"Selected: {item.get('Id', 'unknown')[:8]}")
    
    async def request_confirmation(self, message: str):
        """Request confirmation."""
        self.context.confirmation_message = message
        self.context.state = ConversationState.CONFIRMING.value
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()

    async def request_selection(self, message: str):
        """Request item selection from displayed list."""
        self.context.confirmation_message = message
        self.context.state = ConversationState.SELECTING_ITEM.value
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()

    async def confirm(self) -> bool:
        """User confirmed."""
        if self.context.state != ConversationState.CONFIRMING.value:
            return False
        
        self.context.state = ConversationState.EXECUTING.value
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()
        return True
    
    async def complete(self):
        """Mark flow complete."""
        self.context.state = ConversationState.COMPLETED.value
        self.context.last_updated = datetime.utcnow().isoformat()
        await self.save()
        
        logger.info(f"Flow completed: {self.context.current_flow}")
    
    async def cancel(self):
        """Cancel flow."""
        logger.info(f"Flow cancelled: {self.context.current_flow}")
        await self.clear()
    
    async def reset(self):
        """Reset state."""
        await self.clear()
    
    # === HELPERS ===
    
    def has_all_required_params(self) -> bool:
        """Check if all required params collected."""
        return len(self.context.missing_params) == 0
    
    def parse_item_selection(self, user_input: str) -> Optional[Dict]:
        """Parse item selection from input."""
        if not self.context.displayed_items:
            return None
        
        user_input = user_input.strip().lower()
        
        # Numeric selection
        try:
            index = int(user_input) - 1
            if 0 <= index < len(self.context.displayed_items):
                return self.context.displayed_items[index]
        except ValueError:
            pass
        
        # Name matching
        for item in self.context.displayed_items:
            for name_field in ["name", "Name", "displayName", "DisplayName", "FullVehicleName"]:
                if name_field in item:
                    if user_input in str(item[name_field]).lower():
                        return item
            
            for plate_field in ["plate", "Plate", "LicencePlate"]:
                if plate_field in item:
                    if user_input in str(item[plate_field]).lower():
                        return item
        
        return None
    
    def parse_confirmation(self, user_input: str) -> Optional[bool]:
        """Parse confirmation response."""
        user_input = user_input.strip().lower()
        
        confirm_words = {"da", "potvrdi", "ok", "potvrđujem", "može", "yes", "uredu", "u redu", "y"}
        cancel_words = {"ne", "odustani", "cancel", "odustajem", "prekini", "stop", "no", "n"}
        
        if any(word in user_input for word in confirm_words):
            return True
        
        if any(word in user_input for word in cancel_words):
            return False
        
        return None
    
    def is_timed_out(self) -> bool:
        """Check if flow timed out."""
        if not self.context.started_at:
            return False
        
        try:
            started = datetime.fromisoformat(self.context.started_at)
            elapsed = (datetime.utcnow() - started).total_seconds()
            return elapsed > self.FLOW_TIMEOUT_SECONDS
        except:
            return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return asdict(self.context)
    
    @classmethod
    async def load_for_user(cls, user_id: str, redis_client) -> "ConversationManager":
        """Factory method to load manager for user."""
        manager = cls(user_id, redis_client)
        await manager.load()
        return manager

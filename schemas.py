"""
Pydantic Schemas
Version: 10.0

Data validation schemas.
NO DEPENDENCIES on services.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field


# === ENUMS ===

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class FlowState(str, Enum):
    IDLE = "idle"
    GATHERING = "gathering"
    SELECTING = "selecting"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


# === WEBHOOK SCHEMAS ===

class WebhookResponse(BaseModel):
    """Response to webhook."""
    status: str = "ok"
    message_id: Optional[str] = None


# === USER SCHEMAS ===

class UserData(BaseModel):
    """User information."""
    person_id: str = ""
    phone: str = ""
    display_name: str = "Korisnik"
    tenant_id: str = ""


class VehicleData(BaseModel):
    """Vehicle information."""
    id: str = "UNKNOWN"
    plate: str = "UNKNOWN"
    name: str = "Nepoznato"
    vin: str = ""
    mileage: str = "N/A"


class OperationalContext(BaseModel):
    """User operational context."""
    user: UserData = Field(default_factory=UserData)
    vehicle: VehicleData = Field(default_factory=VehicleData)


# === API RESPONSE ===

class APIResponseSchema(BaseModel):
    """Standardized API response."""
    success: bool
    status_code: int
    data: Optional[Any] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None


# === TOOL SCHEMAS ===

class ToolParameter(BaseModel):
    """Tool parameter."""
    name: str
    type: str
    description: str = ""
    required: bool = False
    enum: Optional[List[str]] = None


class ToolDefinition(BaseModel):
    """Tool definition."""
    operation_id: str
    service: str
    path: str
    method: str
    description: str
    parameters: List[ToolParameter] = []
    auto_inject: List[str] = []


class ToolExecutionRequest(BaseModel):
    """Tool execution request."""
    tool_name: str
    parameters: Dict[str, Any] = {}


class ToolExecutionResponse(BaseModel):
    """Tool execution response."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


# === FLOW SCHEMAS ===

class FlowContext(BaseModel):
    """Flow context."""
    user_id: str
    person_id: str
    tenant_id: str
    state: FlowState = FlowState.IDLE
    parameters: Dict[str, Any] = {}
    displayed_items: List[Dict] = []
    selected_item: Optional[Dict] = None
    metadata: Dict[str, Any] = {}


class FlowResult(BaseModel):
    """Flow processing result."""
    response: str
    state: FlowState = FlowState.IDLE
    is_complete: bool = False
    needs_continuation: bool = False
    data: Dict[str, Any] = {}
    error: Optional[str] = None

"""
Pydantic Schemas
Version: 11.0 (Universal Agent Edition)

Data validation schemas including Flow and Webhook structures.
NO DEPENDENCIES on services.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, model_validator


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


# === INFOBIP WEBHOOK SCHEMAS ===

class InfobipMessage(BaseModel):
    """Model za Infobip poruku s automatskim prepoznavanjem formata teksta."""
    sender: str
    message_id: str = Field(..., alias="messageId")
    message_count: int = Field(..., alias="messageCount")
    text: Optional[str] = None
    content: Optional[List[Dict[str, Any]]] = None
    extracted_text: str = ""

    @model_validator(mode='after')
    def extract_message_text(self) -> 'InfobipMessage':
        """Izvlači tekst bez obzira šalje li Infobip 'text' ili 'content' listu."""
        text = ""
        if self.content and isinstance(self.content, list) and len(self.content) > 0:
            first = self.content[0]
            if first.get("type") == "TEXT":
                text = first.get("text", "")
        elif self.text:
            text = self.text
        
        self.extracted_text = text.strip()
        return self


class InfobipWebhookPayload(BaseModel):
    """Payload koji FastAPI automatski validira."""
    results: List[InfobipMessage]


class WebhookResponse(BaseModel):
    status: str = "ok"
    message_id: Optional[str] = None


# === USER & CONTEXT SCHEMAS ===

class UserData(BaseModel):
    person_id: str = ""
    phone: str = ""
    display_name: str = "Korisnik"
    tenant_id: str = ""


class VehicleData(BaseModel):
    id: str = "UNKNOWN"
    plate: str = "UNKNOWN"
    name: str = "Nepoznato"
    vin: str = ""
    mileage: str = "N/A"


class OperationalContext(BaseModel):
    user: UserData = Field(default_factory=UserData)
    vehicle: VehicleData = Field(default_factory=VehicleData)


# === API & TOOL SCHEMAS ===

class APIResponseSchema(BaseModel):
    success: bool
    status_code: int
    data: Optional[Any] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None


class ToolParameter(BaseModel):
    name: str
    type: str
    description: str = ""
    required: bool = False
    enum: Optional[List[str]] = None


class ToolDefinition(BaseModel):
    operation_id: str
    service: str
    path: str
    method: str
    description: str
    parameters: List[ToolParameter] = []
    auto_inject: List[str] = []
    static_defaults: Dict[str, Any] = {} # Ključno za Zero Hardcoding


# === FLOW SCHEMAS (Vraćeno i integrirano) ===

class FlowContext(BaseModel):
    """Spremnik za memoriju Agenta tijekom ulančavanja alata."""
    user_id: str
    person_id: str
    tenant_id: str
    state: FlowState = FlowState.IDLE
    parameters: Dict[str, Any] = {}
    displayed_items: List[Dict] = []
    selected_item: Optional[Dict] = None
    metadata: Dict[str, Any] = {}


class FlowResult(BaseModel):
    """Rezultat jedne iteracije Agenta."""
    response: str
    state: FlowState = FlowState.IDLE
    is_complete: bool = False
    needs_continuation: bool = False
    data: Dict[str, Any] = {}
    error: Optional[str] = None
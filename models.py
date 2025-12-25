"""
Database Models
Version: 10.0

SQLAlchemy ORM models.
DEPENDS ON: database.py
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Text,
    Integer,
    ForeignKey,
    Index,
    JSON
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class UserMapping(Base):
    """Maps phone numbers to MobilityOne person IDs."""
    
    __tablename__ = "user_mappings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)
    api_identity = Column(String(100), nullable=False, index=True)
    display_name = Column(String(200), nullable=True)
    tenant_id = Column(String(100), nullable=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_user_phone_active", "phone_number", "is_active"),
    )


class Conversation(Base):
    """Conversation metadata."""
    
    __tablename__ = "conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_mappings.id"), nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="active")
    flow_type = Column(String(50), nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    
    __table_args__ = (
        Index("ix_conv_user_status", "user_id", "status"),
    )


class Message(Base):
    """Individual messages."""
    
    __tablename__ = "messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    tool_name = Column(String(100), nullable=True)
    tool_call_id = Column(String(100), nullable=True)
    tool_result = Column(JSON, nullable=True)
    
    __table_args__ = (
        Index("ix_msg_conv_time", "conversation_id", "timestamp"),
    )


class ToolExecution(Base):
    """Tool execution logs."""
    
    __tablename__ = "tool_executions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_name = Column(String(100), nullable=False, index=True)
    parameters = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    executed_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Audit trail."""
    
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(String(100), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_audit_created", "created_at"),
    )

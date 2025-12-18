"""
Queue Service
Version: 10.0

Redis message queues.
NO DEPENDENCIES on other services.
"""

import json
import uuid
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Queue names
STREAM_INBOUND = "whatsapp_stream_inbound"
QUEUE_OUTBOUND = "whatsapp_outbound"
QUEUE_DLQ = "dlq:inbound"


class QueueService:
    """Redis queue management."""
    
    def __init__(self, redis_client):
        """
        Initialize queue service.
        
        Args:
            redis_client: Redis async client
        """
        self.redis = redis_client
    
    async def enqueue_inbound(
        self,
        sender: str,
        text: str,
        message_id: str
    ) -> str:
        """
        Add inbound message to stream.
        
        Args:
            sender: Sender phone number
            text: Message text
            message_id: Message ID
            
        Returns:
            Stream entry ID
        """
        try:
            payload = {
                "sender": sender,
                "text": text,
                "message_id": message_id,
                "retry_count": "0"
            }
            entry_id = await self.redis.xadd(STREAM_INBOUND, payload)
            logger.debug(f"Enqueued inbound: {entry_id}")
            return entry_id
        except Exception as e:
            logger.error(f"Enqueue inbound failed: {e}")
            raise
    
    async def enqueue_outbound(
        self,
        to: str,
        text: str,
        correlation_id: Optional[str] = None,
        attempts: int = 0
    ) -> None:
        """
        Add outbound message to queue.
        
        Args:
            to: Recipient phone number
            text: Message text
            correlation_id: Correlation ID for tracking
            attempts: Number of previous attempts
        """
        try:
            payload = {
                "to": to,
                "text": text,
                "cid": correlation_id or str(uuid.uuid4()),
                "attempts": attempts
            }
            await self.redis.rpush(QUEUE_OUTBOUND, json.dumps(payload))
            logger.debug(f"Enqueued outbound to {to[-4:]}")
        except Exception as e:
            logger.error(f"Enqueue outbound failed: {e}")
            raise
    
    # Alias for compatibility
    async def enqueue(self, to: str, text: str, **kwargs) -> None:
        """Alias for enqueue_outbound."""
        await self.enqueue_outbound(to, text, **kwargs)
    
    async def dequeue_outbound(self, timeout: int = 1) -> Optional[Dict[str, Any]]:
        """
        Get next outbound message.
        
        Args:
            timeout: Blocking timeout in seconds
            
        Returns:
            Message payload or None
        """
        try:
            result = await self.redis.blpop(QUEUE_OUTBOUND, timeout=timeout)
            if result:
                _, data = result
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Dequeue outbound failed: {e}")
            return None
    
    async def store_dlq(self, payload: Dict, error: str) -> None:
        """
        Store failed message in dead letter queue.
        
        Args:
            payload: Original message payload
            error: Error description
        """
        try:
            entry = {
                "original": payload,
                "error": str(error)
            }
            await self.redis.rpush(QUEUE_DLQ, json.dumps(entry))
            logger.warning(f"Message stored in DLQ: {error[:100]}")
        except Exception as e:
            logger.error(f"DLQ store failed: {e}")
    
    async def create_consumer_group(
        self,
        stream: str = STREAM_INBOUND,
        group: str = "workers"
    ) -> bool:
        """
        Create consumer group for stream.
        
        Args:
            stream: Stream name
            group: Group name
            
        Returns:
            True if created or already exists
        """
        try:
            await self.redis.xgroup_create(stream, group, "$", mkstream=True)
            logger.info(f"Created consumer group: {group}")
            return True
        except Exception as e:
            if "BUSYGROUP" in str(e):
                return True
            logger.error(f"Create consumer group failed: {e}")
            return False
    
    async def read_stream(
        self,
        group: str,
        consumer: str,
        count: int = 5,
        block: int = 1000
    ) -> List[Tuple[str, Dict]]:
        """
        Read from stream as consumer.
        
        Args:
            group: Consumer group name
            consumer: Consumer name
            count: Max messages to read
            block: Block timeout in ms
            
        Returns:
            List of (message_id, data) tuples
        """
        try:
            streams = await self.redis.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={STREAM_INBOUND: ">"},
                count=count,
                block=block
            )
            
            if not streams:
                return []
            
            results = []
            for stream_name, messages in streams:
                for msg_id, data in messages:
                    results.append((msg_id, data))
            
            return results
        except Exception as e:
            logger.error(f"Read stream failed: {e}")
            return []
    
    async def ack_message(self, message_id: str) -> bool:
        """
        Acknowledge processed message.
        
        Args:
            message_id: Message ID to ack
            
        Returns:
            True if successful
        """
        try:
            await self.redis.xack(STREAM_INBOUND, "workers", message_id)
            await self.redis.xdel(STREAM_INBOUND, message_id)
            return True
        except Exception as e:
            logger.warning(f"Ack failed: {e}")
            return False

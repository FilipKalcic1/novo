"""
Background Worker
Version: 11.0

Processes messages from Redis queue.

CRITICAL FIXES:
1. CONCURRENT processing - multiple messages at once
2. GRACEFUL shutdown - finishes current tasks before exit
3. Proper signal handling
4. Singleton services
"""

import asyncio
import signal
import logging
import time
import json
import sys
from datetime import datetime
from typing import Optional, Set
from contextlib import suppress

import httpx
import redis.asyncio as aioredis

from config import get_settings

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Handles graceful shutdown signals."""
    
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.active_tasks: Set[asyncio.Task] = set()
    
    def request_shutdown(self):
        """Signal shutdown request."""
        logger.info("🛑 Shutdown requested...")
        self.shutdown_event.set()
    
    def is_shutting_down(self) -> bool:
        """Check if shutdown was requested."""
        return self.shutdown_event.is_set()
    
    async def wait_for_shutdown(self):
        """Wait for shutdown signal."""
        await self.shutdown_event.wait()
    
    def track_task(self, task: asyncio.Task):
        """Track active task."""
        self.active_tasks.add(task)
        task.add_done_callback(self.active_tasks.discard)
    
    async def wait_for_tasks(self, timeout: float = 30.0):
        """Wait for active tasks to complete."""
        if not self.active_tasks:
            return
        
        logger.info(f"⏳ Waiting for {len(self.active_tasks)} active tasks...")
        
        try:
            await asyncio.wait_for(
                asyncio.gather(*self.active_tasks, return_exceptions=True),
                timeout=timeout
            )
            logger.info("✅ All tasks completed")
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ Timeout waiting for tasks, cancelling...")
            for task in self.active_tasks:
                task.cancel()


class Worker:
    """
    Background message processor with concurrent execution.
    
    Features:
    - Concurrent message processing (up to MAX_CONCURRENT)
    - Graceful shutdown with task completion
    - Singleton services (loaded once)
    - Rate limiting per user
    """
    
    MAX_CONCURRENT = 5  # Process up to 5 messages concurrently
    
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.shutdown = GracefulShutdown()
        self.consumer_name = f"worker_{int(datetime.utcnow().timestamp())}"
        self.group_name = "workers"
        
        # Rate limiting
        self._rate_limits: dict = {}
        self.rate_limit = settings.RATE_LIMIT_PER_MINUTE
        self.rate_window = settings.RATE_LIMIT_WINDOW
        
        # Singleton services
        self._gateway = None
        self._registry = None
        
        # Stats
        self._messages_processed = 0
        self._messages_failed = 0
        self._start_time = None
        
        # Semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
    
    async def start(self):
        """Start the worker."""
        self._start_time = datetime.utcnow()
        logger.info(f"🚀 Worker starting: {self.consumer_name}")
        logger.info(f"📊 Max concurrent: {self.MAX_CONCURRENT}")
        
        # Setup signal handlers
        self._setup_signals()
        
        # Initialize connections
        await self._wait_for_redis()
        await self._wait_for_database()
        await self._init_services()
        await self._create_consumer_group()
        
        logger.info("🎉 Worker ready!")
        
        # Run processing loops
        try:
            await asyncio.gather(
                self._process_inbound_loop(),
                self._process_outbound_loop(),
                self._health_reporter(),
                self._shutdown_watcher()
            )
        except asyncio.CancelledError:
            logger.info("Worker tasks cancelled")
    
    def _setup_signals(self):
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.shutdown.request_shutdown)
        
        logger.info("✅ Signal handlers installed")
    
    async def _shutdown_watcher(self):
        """Watch for shutdown and cleanup."""
        await self.shutdown.wait_for_shutdown()
        
        logger.info("🛑 Initiating graceful shutdown...")
        
        # Wait for active tasks
        await self.shutdown.wait_for_tasks(timeout=30.0)
        
        # Cleanup
        await self._cleanup()
    
    async def _cleanup(self):
        """Cleanup resources."""
        logger.info("🧹 Cleaning up...")
        
        # Print stats
        uptime = (datetime.utcnow() - self._start_time).total_seconds() if self._start_time else 0
        logger.info(f"📊 Stats: {self._messages_processed} processed, {self._messages_failed} failed")
        logger.info(f"⏱️ Uptime: {uptime:.0f}s")
        
        if self._gateway:
            await self._gateway.close()
        
        if self.redis:
            await self.redis.aclose()
        
        logger.info("👋 Worker stopped")
    
    async def _wait_for_redis(self, max_retries: int = 30, delay: int = 2):
        """Wait for Redis."""
        for attempt in range(max_retries):
            if self.shutdown.is_shutting_down():
                raise asyncio.CancelledError()
            
            try:
                self.redis = aioredis.from_url(
                    settings.REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True,
                    max_connections=20
                )
                await self.redis.ping()
                logger.info("✅ Redis connected")
                return
            except Exception as e:
                logger.warning(f"Redis not ready ({attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(delay)
        
        raise RuntimeError("Could not connect to Redis")
    
    async def _wait_for_database(self, max_retries: int = 30, delay: int = 2):
        """Wait for database."""
        from database import engine
        from sqlalchemy import text
        
        for attempt in range(max_retries):
            if self.shutdown.is_shutting_down():
                raise asyncio.CancelledError()
            
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                logger.info("✅ Database connected")
                return
            except Exception as e:
                logger.warning(f"Database not ready ({attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(delay)
        
        raise RuntimeError("Could not connect to database")
    
    async def _init_services(self):
        """Initialize singleton services."""
        logger.info("🔧 Initializing services...")
        
        from services.api_gateway import APIGateway
        from services.tool_registry import ToolRegistry
        
        self._gateway = APIGateway(redis_client=self.redis)
        self._registry = ToolRegistry(redis_client=self.redis)
        
        # Load swagger specs
        for source in settings.swagger_sources:
            if self.shutdown.is_shutting_down():
                raise asyncio.CancelledError()
            
            try:
                await self._registry.load_swagger(source)
                logger.info(f"✅ Loaded: {source.split('/')[-3]}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load {source}: {e}")
        
        logger.info(f"✅ Tool Registry: {len(self._registry.tools)} tools")
    
    async def _create_consumer_group(self):
        """Create Redis consumer group."""
        try:
            await self.redis.xgroup_create(
                "whatsapp_stream_inbound",
                self.group_name,
                "$",
                mkstream=True
            )
            logger.info(f"✅ Consumer group created: {self.group_name}")
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.info(f"✅ Consumer group exists: {self.group_name}")
    
    async def _process_inbound_loop(self):
        """Process inbound messages with concurrency."""
        logger.info("📥 Inbound processor started")
        
        while not self.shutdown.is_shutting_down():
            try:
                streams = await self.redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={"whatsapp_stream_inbound": ">"},
                    count=self.MAX_CONCURRENT,
                    block=1000
                )
                
                if not streams:
                    continue
                
                # Process messages concurrently
                tasks = []
                for stream_name, messages in streams:
                    for msg_id, data in messages:
                        task = asyncio.create_task(
                            self._handle_message_safe(msg_id, data)
                        )
                        self.shutdown.track_task(task)
                        tasks.append(task)
                
                # Wait for batch to complete
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Inbound loop error: {e}")
                await asyncio.sleep(2)
    
    async def _handle_message_safe(self, msg_id: str, data: dict):
        """Handle message with semaphore for concurrency control."""
        async with self._semaphore:
            await self._handle_message(msg_id, data)
    
    async def _handle_message(self, msg_id: str, data: dict):
        """Handle single message."""
        sender = data.get("sender", "")
        text = data.get("text", "")
        message_id = data.get("message_id", "")
        
        logger.info(f"📨 Processing: {sender[-4:]} - {text[:30]}")
        
        # Rate limiting
        if not self._check_rate_limit(sender):
            logger.warning(f"⚠️ Rate limited: {sender[-4:]}")
            await self._ack_message(msg_id)
            return
        
        start_time = time.time()
        
        try:
            response = await self._process_message(sender, text, message_id)
            
            if response:
                await self._enqueue_outbound(sender, response)
            
            self._messages_processed += 1
            
        except Exception as e:
            logger.error(f"❌ Processing error: {e}", exc_info=True)
            self._messages_failed += 1
            await self._store_dlq(data, str(e))
        
        finally:
            await self._ack_message(msg_id)
            elapsed = time.time() - start_time
            logger.info(f"✅ Processed in {elapsed:.2f}s")
    
    async def _process_message(self, sender: str, text: str, message_id: str) -> Optional[str]:
        """Process message through engine."""
        from services.queue_service import QueueService
        from services.cache_service import CacheService
        from services.context_service import ContextService
        from services.message_engine import MessageEngine
        from database import AsyncSessionLocal
        
        async with AsyncSessionLocal() as db:
            queue = QueueService(self.redis)
            cache = CacheService(self.redis)
            context = ContextService(self.redis)
            
            engine = MessageEngine(
                gateway=self._gateway,
                registry=self._registry,
                context_service=context,
                queue_service=queue,
                cache_service=cache,
                db_session=db
            )
            
            return await engine.process(sender, text, message_id)
    
    async def _process_outbound_loop(self):
        """Process outbound messages."""
        logger.info("📤 Outbound processor started")
        
        while not self.shutdown.is_shutting_down():
            try:
                result = await self.redis.blpop("whatsapp_outbound", timeout=1)
                
                if not result:
                    continue
                
                _, data = result
                payload = json.loads(data)
                
                await self._send_whatsapp(
                    to=payload.get("to"),
                    text=payload.get("text")
                )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Outbound error: {e}")
                await asyncio.sleep(1)
    
    async def _health_reporter(self):
        """Periodic health report."""
        while not self.shutdown.is_shutting_down():
            try:
                await asyncio.sleep(60)
                
                if self.shutdown.is_shutting_down():
                    break
                
                active = len(self.shutdown.active_tasks)
                logger.info(
                    f"💓 Health: {self._messages_processed} ok, "
                    f"{self._messages_failed} failed, "
                    f"{active} active, "
                    f"{len(self._registry.tools)} tools"
                )
            except asyncio.CancelledError:
                break
    
    async def _send_whatsapp(self, to: str, text: str):
        """Send WhatsApp message."""
        if not settings.INFOBIP_API_KEY:
            logger.warning("⚠️ No Infobip API key")
            return
        
        url = f"https://{settings.INFOBIP_BASE_URL}/whatsapp/1/message/text"
        
        headers = {
            "Authorization": f"App {settings.INFOBIP_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "from": settings.INFOBIP_SENDER_NUMBER,
            "to": to,
            "content": {"text": text}
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code == 200:
                    logger.info(f"📤 Sent to {to[-4:]}")
                else:
                    logger.error(f"Send failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Send error: {e}")
    
    async def _enqueue_outbound(self, to: str, text: str):
        """Enqueue outbound message."""
        payload = {"to": to, "text": text}
        await self.redis.rpush("whatsapp_outbound", json.dumps(payload))
    
    async def _ack_message(self, msg_id: str):
        """Acknowledge message."""
        with suppress(Exception):
            await self.redis.xack("whatsapp_stream_inbound", self.group_name, msg_id)
            await self.redis.xdel("whatsapp_stream_inbound", msg_id)
    
    async def _store_dlq(self, data: dict, error: str):
        """Store in dead letter queue."""
        entry = {
            "original": data,
            "error": error,
            "time": datetime.utcnow().isoformat(),
            "worker": self.consumer_name
        }
        await self.redis.rpush("dlq:inbound", json.dumps(entry))
    
    def _check_rate_limit(self, identifier: str) -> bool:
        """Check rate limit."""
        now = time.time()
        window_start = now - self.rate_window
        
        if identifier in self._rate_limits:
            self._rate_limits[identifier] = [
                t for t in self._rate_limits[identifier]
                if t > window_start
            ]
        else:
            self._rate_limits[identifier] = []
        
        if len(self._rate_limits[identifier]) >= self.rate_limit:
            return False
        
        self._rate_limits[identifier].append(now)
        return True


async def main():
    """Main entry point."""
    worker = Worker()
    
    try:
        await worker.start()
    except asyncio.CancelledError:
        logger.info("Worker cancelled")
    except Exception as e:
        logger.error(f"❌ Worker fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
    
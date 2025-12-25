"""
Background Worker
Version: 12.0

Processes messages from Redis queue.

CRITICAL FIXES v12.0:
1. CONCURRENT processing - multiple messages at once
2. GRACEFUL shutdown - finishes current tasks before exit
3. Proper signal handling
4. Singleton services
5. NEW: Message deduplication lock (prevents double execution)
6. NEW: WhatsAppService integration (phone validation, UTF-8 safe)
7. NEW: Exponential backoff for 429 errors
"""

import asyncio
import signal
import logging
import time
import json
import sys
import hashlib
from datetime import datetime
from typing import Optional, Set, Dict
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

# Reduce noise from verbose libraries (CRITICAL for production readability)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

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
    - NEW v12.0: Message deduplication lock (prevents double execution)
    - NEW v12.0: WhatsAppService for validated sending
    """

    MAX_CONCURRENT = 5  # Process up to 5 messages concurrently
    MESSAGE_LOCK_TTL = 300  # FIX v13.2: Increased to 5 minutes (was 60s)

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.shutdown = GracefulShutdown()
        self.consumer_name = f"worker_{int(datetime.utcnow().timestamp())}"
        self.group_name = "workers"

        # Rate limiting
        self._rate_limits: dict = {}
        self.rate_limit = settings.RATE_LIMIT_PER_MINUTE
        self.rate_window = settings.RATE_LIMIT_WINDOW

        # Singleton services (initialized once at startup)
        self._gateway = None
        self._registry = None
        self._message_engine = None  # CRITICAL: Singleton MessageEngine
        self._whatsapp_service = None  # NEW: WhatsApp integration

        # Per-request services (thread-safe)
        self._queue = None
        self._cache = None
        self._context = None

        # Stats
        self._messages_processed = 0
        self._messages_failed = 0
        self._duplicates_skipped = 0  # NEW: Track duplicates
        self._start_time = None

        # Semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

        # NEW v12.0: In-memory lock for active processing
        self._processing_locks: Dict[str, asyncio.Lock] = {}
    
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
        """
        Initialize singleton services.

        CRITICAL: Services are initialized ONCE at startup, not per-request.
        This ensures:
        - Cache persistence across messages
        - Reduced memory overhead
        - Consistent state

        NEW v12.0: Initializes WhatsAppService for validated sending
        """
        logger.info("🔧 Initializing singleton services...")

        from services.api_gateway import APIGateway
        from services.tool_registry import ToolRegistry
        from services.queue_service import QueueService
        from services.cache_service import CacheService
        from services.context_service import ContextService
        from services.message_engine import MessageEngine
        from services.whatsapp_service import WhatsAppService
        from database import AsyncSessionLocal

        # 1. Initialize core services (shared across all requests)
        self._gateway = APIGateway(redis_client=self.redis)
        self._registry = ToolRegistry(redis_client=self.redis)

        # 2. Initialize per-request services (thread-safe via Redis)
        self._queue = QueueService(self.redis)
        self._cache = CacheService(self.redis)
        self._context = ContextService(self.redis)

        # 3. NEW v12.0: Initialize WhatsApp service
        self._whatsapp_service = WhatsAppService()
        health = self._whatsapp_service.health_check()
        if health["healthy"]:
            logger.info("✅ WhatsAppService initialized and healthy")
        else:
            logger.warning(f"⚠️ WhatsAppService unhealthy: {health}")

        # 4. CRITICAL FIX: Use initialize() with ALL sources at once
        # (Not load_swagger() in loop - that overwrites cache!)
        swagger_sources = settings.swagger_sources
        if not swagger_sources:
            logger.warning("⚠️ No Swagger sources configured")
        else:
            logger.info(f"Initializing registry with {len(swagger_sources)} sources...")
            success = await self._registry.initialize(swagger_sources)

            if success:
                logger.info(
                    f"✅ Tool Registry: {len(self._registry.tools)} tools loaded"
                )

                # v13.0: Initialize API Capability Registry for dynamic learning
                from services.api_capabilities import initialize_capability_registry
                capability_registry = await initialize_capability_registry(self._registry)
                logger.info(
                    f"✅ API Capabilities: {len(capability_registry.capabilities)} tools analyzed"
                )
            else:
                logger.error("❌ Tool Registry initialization failed")
                raise RuntimeError("Tool Registry initialization failed")

        # 5. CRITICAL FIX: Initialize MessageEngine ONCE (singleton pattern)
        # Note: DB session is per-request, so we pass None here
        # and inject it per-message in _process_message
        logger.info("🔧 Initializing MessageEngine singleton...")
        # MessageEngine will get db_session per request, so we create a temp session for init
        async with AsyncSessionLocal() as temp_db:
            self._message_engine = MessageEngine(
                gateway=self._gateway,
                registry=self._registry,
                context_service=self._context,
                queue_service=self._queue,
                cache_service=self._cache,
                db_session=temp_db  # Temp session for init only
            )
            logger.info("✅ MessageEngine initialized")
    
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

    async def _acquire_message_lock(self, sender: str, message_id: str) -> bool:
        """
        Acquire distributed lock to prevent double execution.

        NEW v12.0: Koristi Redis SETNX za atomičku akviziciju locka.
        Ovo sprječava situaciju gdje dva workera procesiraju istu poruku.

        Args:
            sender: Sender phone number
            message_id: Unique message ID

        Returns:
            True if lock acquired, False if message is already being processed
        """
        # Create unique lock key
        lock_key = f"msg_lock:{sender}:{message_id}"

        try:
            # SETNX - Set if Not eXists (atomic operation)
            acquired = await self.redis.set(
                lock_key,
                self.consumer_name,
                nx=True,  # Only set if not exists
                ex=self.MESSAGE_LOCK_TTL  # Auto-expire after TTL
            )

            if acquired:
                logger.debug(f"🔒 Lock acquired: {lock_key}")
                return True
            else:
                # Lock exists - someone else is processing
                holder = await self.redis.get(lock_key)
                logger.warning(
                    f"⚠️ DUPLICATE DETECTED: {lock_key} "
                    f"(held by {holder})"
                )
                return False

        except Exception as e:
            logger.error(f"Lock acquisition error: {e}")
            # On error, allow processing (fail open)
            return True

    async def _release_message_lock(self, sender: str, message_id: str) -> None:
        """Release message processing lock."""
        lock_key = f"msg_lock:{sender}:{message_id}"

        try:
            await self.redis.delete(lock_key)
            logger.debug(f"🔓 Lock released: {lock_key}")
        except Exception as e:
            logger.warning(f"Lock release error: {e}")

    async def _handle_message(self, msg_id: str, data: dict):
        """
        Handle single message with deduplication.

        NEW v12.0:
        1. Acquires distributed lock before processing
        2. Skips if message already being processed
        3. Releases lock on completion

        FIX v13.2:
        - Uses content hash as fallback if message_id missing
        - Prevents duplicate processing from webhook retries
        """
        sender = data.get("sender", "")
        text = data.get("text", "")
        message_id = data.get("message_id", "")

        # FIX v13.2: Generate content hash if message_id is missing or empty
        # This prevents duplicates when webhook is retried by WhatsApp/Infobip
        if not message_id:
            content_hash = hashlib.md5(
                f"{sender}:{text}".encode()
            ).hexdigest()[:16]
            message_id = f"hash_{content_hash}"
            logger.debug(f"Generated content hash as message_id: {message_id}")

        logger.info(f"📨 Processing: {sender[-4:]} - {text[:30]}")

        # NEW v12.0: Check for duplicate processing
        if not await self._acquire_message_lock(sender, message_id):
            self._duplicates_skipped += 1
            logger.warning(
                f"🔁 SKIPPING DUPLICATE: {sender[-4:]} - {message_id[:20]}..."
            )
            await self._ack_message(msg_id)
            return

        # Rate limiting
        if not self._check_rate_limit(sender):
            logger.warning(f"⚠️ Rate limited: {sender[-4:]}")
            await self._release_message_lock(sender, message_id)
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
            # ALWAYS release lock
            await self._release_message_lock(sender, message_id)
            await self._ack_message(msg_id)
            elapsed = time.time() - start_time
            logger.info(f"✅ Processed in {elapsed:.2f}s")
    
    async def _process_message(self, sender: str, text: str, message_id: str) -> Optional[str]:
        """
        Process message through MessageEngine singleton.

        CRITICAL FIX: Uses singleton engine with per-request DB session.
        This ensures:
        - Shared cache between messages (performance)
        - Fresh DB session per request (isolation)
        - Correct transaction boundaries
        """
        from database import AsyncSessionLocal

        # Create fresh DB session for this request
        async with AsyncSessionLocal() as db:
            # Inject fresh DB session into existing engine
            # (Services like context, queue, cache are already singleton)
            self._message_engine.db = db

            try:
                return await self._message_engine.process(sender, text, message_id)
            except Exception as e:
                # Ensure rollback on error
                await db.rollback()
                logger.error(f"Message processing error, rolled back transaction: {e}")
                raise
    
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
        """Periodic health report with extended stats."""
        while not self.shutdown.is_shutting_down():
            try:
                await asyncio.sleep(60)

                if self.shutdown.is_shutting_down():
                    break

                active = len(self.shutdown.active_tasks)

                # NEW v12.0: Include WhatsApp stats and duplicate count
                whatsapp_stats = {}
                if self._whatsapp_service:
                    whatsapp_stats = self._whatsapp_service.get_stats()

                logger.info(
                    f"💓 Health: "
                    f"processed={self._messages_processed}, "
                    f"failed={self._messages_failed}, "
                    f"duplicates_skipped={self._duplicates_skipped}, "
                    f"active={active}, "
                    f"tools={len(self._registry.tools)}, "
                    f"wa_sent={whatsapp_stats.get('messages_sent', 0)}, "
                    f"wa_retries={whatsapp_stats.get('total_retries', 0)}"
                )
            except asyncio.CancelledError:
                break
    
    async def _send_whatsapp(self, to: str, text: str):
        """
        Send WhatsApp message via WhatsAppService.

        NEW v12.0: Uses WhatsAppService which provides:
        - Phone number validation (prevents UUID trap)
        - UTF-8 safe encoding
        - Type guards (ensures text is string)
        - Exponential backoff with jitter
        - Deep logging for debugging
        """
        if not self._whatsapp_service:
            logger.warning("⚠️ WhatsAppService not initialized")
            return

        result = await self._whatsapp_service.send(to, text)

        if result.success:
            logger.info(
                f"📤 Sent to {to[-4:]}... "
                f"(message_id={result.message_id})"
            )
        else:
            logger.error(
                f"❌ Send failed: {result.error_code} - {result.error_message}"
            )

            # Store failed message for retry
            if result.error_code == "RATE_LIMIT":
                # Re-queue with delay for rate limit
                await self._enqueue_outbound_delayed(
                    to, text,
                    delay=result.retry_after or 30
                )

    async def _enqueue_outbound_delayed(
        self,
        to: str,
        text: str,
        delay: int = 30
    ):
        """
        Enqueue outbound message with delay for rate limiting.

        Uses Redis ZADD with score = current_time + delay.
        """
        try:
            delayed_payload = json.dumps({
                "to": to,
                "text": text,
                "scheduled_at": time.time() + delay
            })

            # Use sorted set for delayed processing
            await self.redis.zadd(
                "whatsapp_outbound_delayed",
                {delayed_payload: time.time() + delay}
            )

            logger.info(
                f"⏰ Message queued for retry in {delay}s: {to[-4:]}..."
            )
        except Exception as e:
            logger.error(f"Failed to queue delayed message: {e}")
    
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
    
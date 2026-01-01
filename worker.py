"""
Background Worker
Version: 13.0

Processes messages from Redis queue.
"""

import asyncio
import signal
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

# === KONFIGURACIJA ===
MAX_CONCURRENT = 5              # Ograničeno Azure TPM limitom
MESSAGE_LOCK_TTL = 300          # 5 min - dovoljno za najduže LLM pozive
REDIS_MAX_RETRIES = 30          # 30 x 2s = 60s max čekanja na Redis
REDIS_RETRY_DELAY = 2
HEALTH_REPORT_INTERVAL = 60     # Svake minute
STREAM_BLOCK_MS = 1000          # 1s blocking read


def log(level: str, event: str, data: dict = None):
    """JSON structured logging."""
    print(json.dumps({
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "level": level,
        "event": event,
        "worker": "worker",
        **(data or {})
    }), flush=True)


class GracefulShutdown:
    """Handles graceful shutdown signals."""

    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.active_tasks: Set[asyncio.Task] = set()

    def request_shutdown(self):
        log("info", "shutdown_requested")
        self.shutdown_event.set()

    def is_shutting_down(self) -> bool:
        return self.shutdown_event.is_set()

    async def wait_for_shutdown(self):
        await self.shutdown_event.wait()

    def track_task(self, task: asyncio.Task):
        self.active_tasks.add(task)
        task.add_done_callback(self.active_tasks.discard)

    async def wait_for_tasks(self, timeout: float = 30.0):
        if not self.active_tasks:
            return

        log("info", "waiting_for_tasks", {"count": len(self.active_tasks)})

        try:
            await asyncio.wait_for(
                asyncio.gather(*self.active_tasks, return_exceptions=True),
                timeout=timeout
            )
            log("info", "tasks_completed")
        except asyncio.TimeoutError:
            log("warn", "tasks_timeout_cancelling", {"count": len(self.active_tasks)})
            for task in self.active_tasks:
                task.cancel()


class Worker:
    """Background message processor with concurrent execution."""

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.shutdown = GracefulShutdown()
        self.consumer_name = f"worker_{int(datetime.utcnow().timestamp())}"
        self.group_name = "workers"

        # Rate limiting
        self._rate_limits: dict = {}
        self.rate_limit = settings.RATE_LIMIT_PER_MINUTE
        self.rate_window = settings.RATE_LIMIT_WINDOW
        self._rate_limit_cleanup_counter = 0

        # Singleton services
        self._gateway = None
        self._registry = None
        self._message_engine = None
        self._whatsapp_service = None

        # Per-request services
        self._queue = None
        self._cache = None
        self._context = None

        # Stats
        self._messages_processed = 0
        self._messages_failed = 0
        self._duplicates_skipped = 0
        self._start_time = None

        # Concurrency control
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self._processing_locks: Dict[str, asyncio.Lock] = {}

    async def start(self):
        self._start_time = datetime.utcnow()
        log("info", "worker_starting", {
            "consumer": self.consumer_name,
            "max_concurrent": MAX_CONCURRENT
        })

        self._setup_signals()

        await self._wait_for_redis()
        await self._wait_for_database()
        await self._init_services()
        await self._create_consumer_group()

        log("info", "worker_ready")

        try:
            await asyncio.gather(
                self._process_inbound_loop(),
                self._process_outbound_loop(),
                self._health_reporter(),
                self._shutdown_watcher()
            )
        except asyncio.CancelledError:
            log("info", "worker_cancelled")

    def _setup_signals(self):
        """Setup signal handlers - compatible with Python 3.10+."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.shutdown.request_shutdown)
            log("info", "signals_installed")
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            log("warn", "signals_not_supported")

    async def _shutdown_watcher(self):
        await self.shutdown.wait_for_shutdown()
        log("info", "shutdown_initiated")
        await self.shutdown.wait_for_tasks(timeout=30.0)
        await self._cleanup()

    async def _cleanup(self):
        log("info", "cleanup_started")

        uptime = (datetime.utcnow() - self._start_time).total_seconds() if self._start_time else 0
        log("info", "final_stats", {
            "processed": self._messages_processed,
            "failed": self._messages_failed,
            "duplicates": self._duplicates_skipped,
            "uptime_seconds": int(uptime)
        })

        if self._gateway:
            await self._gateway.close()

        if self.redis:
            await self.redis.aclose()

        log("info", "worker_stopped")

    async def _wait_for_redis(self):
        for attempt in range(REDIS_MAX_RETRIES):
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
                log("info", "redis_connected")
                return
            except Exception as e:
                log("warn", "redis_retry", {
                    "attempt": attempt + 1,
                    "max": REDIS_MAX_RETRIES,
                    "error": str(e)
                })
                await asyncio.sleep(REDIS_RETRY_DELAY)

        raise RuntimeError("Could not connect to Redis")

    async def _wait_for_database(self):
        from database import engine
        from sqlalchemy import text

        for attempt in range(REDIS_MAX_RETRIES):
            if self.shutdown.is_shutting_down():
                raise asyncio.CancelledError()

            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                log("info", "database_connected")
                return
            except Exception as e:
                log("warn", "database_retry", {
                    "attempt": attempt + 1,
                    "max": REDIS_MAX_RETRIES,
                    "error": str(e)
                })
                await asyncio.sleep(REDIS_RETRY_DELAY)

        raise RuntimeError("Could not connect to database")

    async def _init_services(self):
        """Initialize singleton services."""
        log("info", "init_services_started")

        from services.api_gateway import APIGateway
        from services.tool_registry import ToolRegistry
        from services.queue_service import QueueService
        from services.cache_service import CacheService
        from services.context_service import ContextService
        from services.message_engine import MessageEngine
        from services.whatsapp_service import WhatsAppService
        from database import AsyncSessionLocal

        self._gateway = APIGateway(redis_client=self.redis)
        self._registry = ToolRegistry(redis_client=self.redis)

        self._queue = QueueService(self.redis)
        self._cache = CacheService(self.redis)
        self._context = ContextService(self.redis)

        self._whatsapp_service = WhatsAppService()
        health = self._whatsapp_service.health_check()
        log("info", "whatsapp_service_init", {"healthy": health["healthy"]})

        swagger_sources = settings.swagger_sources
        if not swagger_sources:
            log("warn", "no_swagger_sources")
        else:
            log("info", "registry_init", {"sources": len(swagger_sources)})
            success = await self._registry.initialize(swagger_sources)

            if success:
                log("info", "registry_ready", {"tools": len(self._registry.tools)})

                from services.api_capabilities import initialize_capability_registry
                capability_registry = await initialize_capability_registry(self._registry)
                log("info", "capabilities_ready", {
                    "capabilities": len(capability_registry.capabilities)
                })
            else:
                log("error", "registry_failed")
                raise RuntimeError("Tool Registry initialization failed")

        log("info", "message_engine_init")
        async with AsyncSessionLocal() as temp_db:
            self._message_engine = MessageEngine(
                gateway=self._gateway,
                registry=self._registry,
                context_service=self._context,
                queue_service=self._queue,
                cache_service=self._cache,
                db_session=temp_db
            )
            log("info", "message_engine_ready")

    async def _create_consumer_group(self):
        try:
            await self.redis.xgroup_create(
                "whatsapp_stream_inbound",
                self.group_name,
                "$",
                mkstream=True
            )
            log("info", "consumer_group_created", {"group": self.group_name})
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
            log("info", "consumer_group_exists", {"group": self.group_name})

    async def _process_inbound_loop(self):
        log("info", "inbound_processor_started")

        while not self.shutdown.is_shutting_down():
            try:
                streams = await self.redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={"whatsapp_stream_inbound": ">"},
                    count=MAX_CONCURRENT,
                    block=STREAM_BLOCK_MS
                )

                if not streams:
                    continue

                tasks = []
                for stream_name, messages in streams:
                    for msg_id, data in messages:
                        task = asyncio.create_task(
                            self._handle_message_safe(msg_id, data)
                        )
                        self.shutdown.track_task(task)
                        tasks.append(task)

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log("error", "inbound_loop_error", {"error": str(e)})
                await asyncio.sleep(2)

    async def _handle_message_safe(self, msg_id: str, data: dict):
        async with self._semaphore:
            await self._handle_message(msg_id, data)

    async def _acquire_message_lock(self, sender: str, message_id: str) -> bool:
        """Acquire distributed lock to prevent double execution."""
        lock_key = f"msg_lock:{sender}:{message_id}"

        try:
            acquired = await self.redis.set(
                lock_key,
                self.consumer_name,
                nx=True,
                ex=MESSAGE_LOCK_TTL
            )

            if acquired:
                return True
            else:
                holder = await self.redis.get(lock_key)
                log("warn", "duplicate_detected", {
                    "lock_key": lock_key,
                    "holder": holder
                })
                return False

        except Exception as e:
            log("error", "lock_error", {"error": str(e)})
            return True  # Fail open

    async def _release_message_lock(self, sender: str, message_id: str) -> None:
        lock_key = f"msg_lock:{sender}:{message_id}"
        try:
            await self.redis.delete(lock_key)
        except Exception as e:
            log("warn", "lock_release_error", {"error": str(e)})

    async def _handle_message(self, msg_id: str, data: dict):
        """Handle single message with deduplication."""
        sender = data.get("sender", "")
        text = data.get("text", "")
        message_id = data.get("message_id", "")

        if not message_id:
            content_hash = hashlib.md5(
                f"{sender}:{text}".encode()
            ).hexdigest()[:16]
            message_id = f"hash_{content_hash}"

        log("info", "processing", {
            "sender": sender[-4:] if sender else "",
            "text_preview": text[:30] if text else ""
        })

        if not await self._acquire_message_lock(sender, message_id):
            self._duplicates_skipped += 1
            log("warn", "skipping_duplicate", {
                "sender": sender[-4:],
                "message_id": message_id[:20]
            })
            await self._ack_message(msg_id)
            return

        if not self._check_rate_limit(sender):
            log("warn", "rate_limited", {"sender": sender[-4:]})
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
            log("error", "processing_error", {"error": str(e)})
            self._messages_failed += 1
            await self._store_dlq(data, str(e))

        finally:
            await self._release_message_lock(sender, message_id)
            await self._ack_message(msg_id)
            elapsed = time.time() - start_time
            log("info", "processed", {"elapsed_ms": int(elapsed * 1000)})

    async def _process_message(self, sender: str, text: str, message_id: str) -> Optional[str]:
        """Process message through MessageEngine singleton."""
        from database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            self._message_engine.db = db

            try:
                return await self._message_engine.process(sender, text, message_id)
            except Exception as e:
                await db.rollback()
                log("error", "engine_error_rollback", {"error": str(e)})
                raise

    async def _process_outbound_loop(self):
        log("info", "outbound_processor_started")

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
                log("error", "outbound_error", {"error": str(e)})
                await asyncio.sleep(1)

    async def _health_reporter(self):
        while not self.shutdown.is_shutting_down():
            try:
                await asyncio.sleep(HEALTH_REPORT_INTERVAL)

                if self.shutdown.is_shutting_down():
                    break

                active = len(self.shutdown.active_tasks)

                whatsapp_stats = {}
                if self._whatsapp_service:
                    whatsapp_stats = self._whatsapp_service.get_stats()

                log("info", "health", {
                    "processed": self._messages_processed,
                    "failed": self._messages_failed,
                    "duplicates": self._duplicates_skipped,
                    "active_tasks": active,
                    "tools": len(self._registry.tools) if self._registry else 0,
                    "wa_sent": whatsapp_stats.get("messages_sent", 0),
                    "wa_retries": whatsapp_stats.get("total_retries", 0)
                })
            except asyncio.CancelledError:
                break

    async def _send_whatsapp(self, to: str, text: str):
        """Send WhatsApp message via WhatsAppService."""
        if not self._whatsapp_service:
            log("warn", "whatsapp_not_initialized")
            return

        result = await self._whatsapp_service.send(to, text)

        if result.success:
            log("info", "sent", {
                "to": to[-4:] if to else "",
                "message_id": result.message_id
            })
        else:
            log("error", "send_failed", {
                "error_code": result.error_code,
                "error": result.error_message
            })

            if result.error_code == "RATE_LIMIT":
                await self._enqueue_outbound_delayed(
                    to, text,
                    delay=result.retry_after or 30
                )

    async def _enqueue_outbound_delayed(self, to: str, text: str, delay: int = 30):
        """Enqueue outbound message with delay for rate limiting."""
        try:
            delayed_payload = json.dumps({
                "to": to,
                "text": text,
                "scheduled_at": time.time() + delay
            })

            await self.redis.zadd(
                "whatsapp_outbound_delayed",
                {delayed_payload: time.time() + delay}
            )

            log("info", "queued_delayed", {"to": to[-4:], "delay": delay})
        except Exception as e:
            log("error", "queue_delayed_failed", {"error": str(e)})

    async def _enqueue_outbound(self, to: str, text: str):
        payload = {"to": to, "text": text}
        await self.redis.rpush("whatsapp_outbound", json.dumps(payload))

    async def _ack_message(self, msg_id: str):
        with suppress(Exception):
            await self.redis.xack("whatsapp_stream_inbound", self.group_name, msg_id)
            await self.redis.xdel("whatsapp_stream_inbound", msg_id)

    async def _store_dlq(self, data: dict, error: str):
        entry = {
            "original": data,
            "error": error,
            "time": datetime.utcnow().isoformat(),
            "worker": self.consumer_name
        }
        await self.redis.rpush("dlq:inbound", json.dumps(entry))

    def _check_rate_limit(self, identifier: str) -> bool:
        """Check rate limit with periodic cleanup."""
        now = time.time()
        window_start = now - self.rate_window

        self._rate_limit_cleanup_counter += 1
        if self._rate_limit_cleanup_counter >= 100:
            self._rate_limit_cleanup_counter = 0
            stale_keys = [
                k for k, v in self._rate_limits.items()
                if not v or max(v) < window_start
            ]
            for k in stale_keys:
                del self._rate_limits[k]

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
    worker = Worker()

    try:
        await worker.start()
    except asyncio.CancelledError:
        log("info", "worker_cancelled")
    except Exception as e:
        log("error", "worker_fatal", {"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

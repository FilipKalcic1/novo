"""
MobilityOne WhatsApp Bot - FastAPI Application
Version: 11.0

Main entry point with automatic database initialization.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

# Configure logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Reduce noise from verbose libraries (CRITICAL for production readability)
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Import config
from config import get_settings

settings = get_settings()


async def wait_for_database(max_retries: int = 30, delay: int = 2) -> bool:
    """Wait for database to be available and create tables."""
    from database import engine, Base
    from models import UserMapping, Conversation, Message, ToolExecution, AuditLog  # noqa
    
    logger.info("⏳ Waiting for database...")
    
    for attempt in range(max_retries):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            
            logger.info("✅ Database connection established")
            
            # Create tables
            logger.info("📊 Creating database tables...")
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            
            logger.info("✅ Database tables ready")
            return True
            
        except Exception as e:
            logger.warning(f"Database not ready (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
    
    logger.error("❌ Could not connect to database after all retries")
    return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan manager."""
    logger.info("🚀 Starting MobilityOne Bot v11.0...")
    
    # 1. Wait for database and create tables
    db_ready = await wait_for_database()
    if not db_ready:
        logger.error("❌ Cannot start without database")
        raise RuntimeError("Database not available")
    
    # 2. Initialize Redis
    try:
        redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS
        )
        await redis_client.ping()
        app.state.redis = redis_client
        logger.info("✅ Redis connected")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        raise RuntimeError(f"Redis not available: {e}")
    
    # 3. Initialize services
    try:
        from services.api_gateway import APIGateway
        from services.tool_registry import ToolRegistry
        from services.queue_service import QueueService
        from services.cache_service import CacheService
        from services.context_service import ContextService
        
        # API Gateway
        app.state.gateway = APIGateway(redis_client=app.state.redis)
        logger.info("✅ API Gateway initialized")
        
        # Tool Registry
        app.state.registry = ToolRegistry(redis_client=app.state.redis)
        
        # Load swagger specs
        for source in settings.swagger_sources:
            try:
                await app.state.registry.load_swagger(source)
                logger.info(f"✅ Loaded: {source.split('/')[-3]}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load {source}: {e}")
        
        logger.info(f"✅ Tool Registry: {len(app.state.registry.tools)} tools")
        
        # Queue Service
        app.state.queue = QueueService(app.state.redis)
        await app.state.queue.create_consumer_group()
        logger.info("✅ Queue Service initialized")
        
        # Cache Service
        app.state.cache = CacheService(app.state.redis)
        logger.info("✅ Cache Service initialized")
        
        # Context Service
        app.state.context = ContextService(app.state.redis)
        logger.info("✅ Context Service initialized")
        
    except Exception as e:
        logger.error(f"❌ Service initialization failed: {e}")
        raise
    
    logger.info("🎉 Application ready!")
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    
    if hasattr(app.state, 'gateway') and app.state.gateway:
        await app.state.gateway.close()
    
    if hasattr(app.state, 'redis') and app.state.redis:
        await app.state.redis.aclose()
    
    logger.info("👋 Goodbye!")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="WhatsApp Fleet Management Bot",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Simple webhook endpoint that pushes to Redis queue
from webhook_simple import router as webhook_router
app.include_router(webhook_router, prefix="/webhook", tags=["webhook"])

if settings.DEBUG:
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            logger.debug(f"Registered route: {route.path} {list(route.methods) if route.methods else []}")
        else:
            logger.debug(f"Registered non-HTTP route: {route.name if hasattr(route, 'name') else route}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    from database import engine
    
    checks = {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "database": "disconnected",
        "redis": "disconnected",
        "tools": 0
    }
    
    try:
        # Check database
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "connected"
        
        # Check redis
        if hasattr(app.state, 'redis') and app.state.redis:
            await app.state.redis.ping()
            checks["redis"] = "connected"
        
        # Check tools
        if hasattr(app.state, 'registry') and app.state.registry:
            checks["tools"] = len(app.state.registry.tools)
            
    except Exception as e:
        checks["status"] = "unhealthy"
        checks["error"] = str(e)
    
    return checks


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        workers=1
    )

"""
Simple webhook endpoint for WhatsApp messages.
Receives messages and pushes to Redis queue for worker processing.

Version: 2.1
NEW v2.1: Async Redis to avoid blocking FastAPI event loop
NEW v2.0: Validates sender field is not empty (prevents 400 errors downstream)
"""

from fastapi import APIRouter, Request, HTTPException
import redis.asyncio as aioredis
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Async Redis client (lazy initialization)
_redis_client = None


async def get_redis():
    """Get async Redis client (lazy initialization)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(
            "redis://redis:6379/0",
            encoding="utf-8",
            decode_responses=True
        )
    return _redis_client


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Receive WhatsApp webhook messages and push to Redis STREAM.

    CRITICAL FIX: Worker listens on "whatsapp_stream_inbound" stream, NOT list!

    Flow:
    1. Receive webhook from WhatsApp
    2. Extract message data (sender, text, message_id)
    3. VALIDATE sender is present (prevents 400 errors in WhatsApp response)
    4. Push to Redis STREAM: "whatsapp_stream_inbound"
    5. Worker picks up from stream via consumer group
    """
    try:
        body = await request.json()

        logger.info(f"Received WhatsApp webhook: {body}")

        # Extract message details from Infobip format
        results = body.get("results", [])
        if not results:
            logger.warning("No results in webhook body")
            return {"status": "ok"}

        for result in results:
            sender = result.get("sender", "")
            content_list = result.get("content", [])
            message_id = result.get("messageId", "")

            # CRITICAL v2.0: Validate sender is present
            # Without sender, we cannot reply - this would cause 400 error
            if not sender:
                logger.error(
                    "MISSING SENDER in webhook! "
                    f"message_id={message_id}, content_types={[c.get('type') for c in content_list]}"
                )
                continue

            # Extract text from content
            text = ""
            for content in content_list:
                if content.get("type") == "TEXT":
                    text = content.get("text", "")
                    break

            if not text:
                # Log what type of content we received (image, location, etc.)
                content_types = [c.get("type") for c in content_list]
                logger.warning(
                    f"No text content in message from {sender[-4:]}... "
                    f"Content types: {content_types}"
                )
                continue

            # Push to Redis STREAM (not list!) - this is what worker listens to
            stream_data = {
                "sender": sender,
                "text": text,
                "message_id": message_id
            }

            redis = await get_redis()
            await redis.xadd("whatsapp_stream_inbound", stream_data)

            logger.info(f"Message pushed to stream: {sender[-4:]}... - {text[:30]}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/whatsapp")
async def whatsapp_webhook_verify(request: Request):
    """
    WhatsApp webhook verification endpoint.
    """
    # WhatsApp verification
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == "your_verify_token":
        logger.info("WhatsApp webhook verified")
        return int(challenge)

    logger.warning("WhatsApp webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")

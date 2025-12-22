"""
Simple webhook endpoint for WhatsApp messages.
Receives messages and pushes to Redis queue for worker processing.
"""

from fastapi import APIRouter, Request, HTTPException
from redis import Redis
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis connection
redis_client = Redis(host="redis", port=6379, db=0, decode_responses=True)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Receive WhatsApp webhook messages and push to Redis STREAM.

    CRITICAL FIX: Worker listens on "whatsapp_stream_inbound" stream, NOT list!

    Flow:
    1. Receive webhook from WhatsApp
    2. Extract message data (sender, text, message_id)
    3. Push to Redis STREAM: "whatsapp_stream_inbound"
    4. Worker picks up from stream via consumer group
    """
    try:
        body = await request.json()

        logger.info(f"📩 Received WhatsApp webhook: {body}")

        # Extract message details from Infobip format
        results = body.get("results", [])
        if not results:
            logger.warning("⚠️ No results in webhook body")
            return {"status": "ok"}

        for result in results:
            sender = result.get("sender", "")
            content_list = result.get("content", [])
            message_id = result.get("messageId", "")

            # Extract text from content
            text = ""
            for content in content_list:
                if content.get("type") == "TEXT":
                    text = content.get("text", "")
                    break

            if not text:
                logger.warning("⚠️ No text content in message")
                continue

            # Push to Redis STREAM (not list!) - this is what worker listens to
            stream_data = {
                "sender": sender,
                "text": text,
                "message_id": message_id
            }

            redis_client.xadd("whatsapp_stream_inbound", stream_data)

            logger.info(f"✅ Message pushed to stream: {sender[-4:]} - {text[:30]}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
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
        logger.info("✅ WhatsApp webhook verified")
        return int(challenge)

    logger.warning("⚠️ WhatsApp webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")

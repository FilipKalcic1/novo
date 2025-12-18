import uuid
import structlog
import json
from fastapi import APIRouter, Depends, Request, HTTPException
from services.queue_service import QueueService
from security import verify_webhook
from fastapi_limiter.depends import RateLimiter

router = APIRouter()
logger = structlog.get_logger("webhook")

# Dependency Injection
def get_queue(request: Request) -> QueueService: 
    return request.app.state.queue

@router.post(
    "/whatsapp", 
    dependencies=[
        Depends(verify_webhook),  # fixaj ovo 
        Depends(RateLimiter(times=100, minutes=1)) 
    ]
)
async def whatsapp_webhook(request: Request, queue: QueueService = Depends(get_queue)):
    try:
        payload = await request.json()
        logger.info("🔥 RAW INFOBIP PAYLOAD 🔥", payload=payload) 
    except Exception as e:
        logger.error("Failed to parse JSON", error=str(e))
        return {"status": "error", "reason": "invalid_json"}

    results = payload.get("results", [])
    if not results:
        logger.warning("Ignoriram payload (prazna 'results' lista)", payload=payload)
        return {"status": "ignored", "reason": "empty_results"}

    msg = results[0]
    
    sender = msg.get("sender") or msg.get("from")
    message_id = msg.get("messageId") or str(uuid.uuid4())

    if not sender:
        logger.warning("Ignoriram poruku (fali pošiljatelj)", msg=msg)
        return {"status": "ignored", "reason": "missing_sender"}

    text = ""
    content_type = "UNKNOWN"

    # Handle modern list-based content
    if "content" in msg and isinstance(msg["content"], list) and msg["content"]:
        first_content = msg["content"][0]
        content_type = first_content.get("type", "UNKNOWN")
        if content_type == "TEXT":
            text = first_content.get("text", "").strip()
    # Fallback for older, simple text format
    elif "text" in msg:
        text = msg.get("text", "").strip()
        content_type = "TEXT" # Assume text if 'text' field is present
    
    if not text:
        logger.warning(
            "Ignoriram poruku (prazan tekst ili nepodržan tip)", 
            sender=sender, 
            content_type=content_type,
            msg=msg
        )
        return {"status": "ignored", "reason": f"empty_text_or_unsupported_type:{content_type}"}

    logger.info("✅ Poruka uspješno pročitana", sender=sender, text=text)
    
    stream_id = await queue.enqueue_inbound(
        sender=sender, 
        text=text, 
        message_id=message_id
    )
    
    return {"status": "queued", "stream_id": stream_id}
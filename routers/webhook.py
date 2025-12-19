"""
Webhook Router
Version: 11.0 (Complete & Robust)
"""
import structlog
from fastapi import APIRouter, Depends, Request, BackgroundTasks, status
from services.queue_service import QueueService
from security import verify_webhook, sanitize_phone
from schemas import InfobipWebhookPayload

router = APIRouter()
logger = structlog.get_logger("webhook")

def get_queue(request: Request) -> QueueService:
    # Dohvaća queue servis inicijaliziran u main.py
    return request.app.state.queue


@router.post(


    "/whatsapp",


    status_code=status.HTTP_200_OK,


    dependencies=[Depends(verify_webhook)] # Propušta auth ako je DEBUG=True


)


async def whatsapp_webhook(


    payload: InfobipWebhookPayload,


    background_tasks: BackgroundTasks,


    queue: QueueService = Depends(get_queue)


):


    """


    Prima poruke od Infobipa, validira ih putem Pydantica i asinkrono šalje u Redis.


    Vraća 200 OK odmah (ispod 50ms).


    """


    if not payload.results:


        logger.warning("Primljen prazan webhook payload")


        return {"status": "ignored", "reason": "empty_results"}





    for msg in payload.results:


        # Pydantic je već obradio ekstrakciju teksta u msg.extracted_text


        if not msg.extracted_text:


            logger.debug("Preskačem poruku bez teksta", id=msg.message_id)


            continue





        sender = sanitize_phone(msg.sender)


        


        logger.info("📩 Poruka primljena", 


                    sender=sender, 


                    message_id=msg.message_id,


                    text_preview=msg.extracted_text[:30])





        # BackgroundTask osigurava da spora AI obrada ne blokira odgovor Infobipu


        background_tasks.add_task(


            queue.enqueue_inbound,


            sender=sender,


            text=msg.extracted_text,


            message_id=msg.message_id


        )





    return {


        "status": "queued", 


        "count": len(payload.results)


    }
"""Quick booking flow test with real phone number."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def test():
    import redis.asyncio as aioredis
    from config import get_settings
    from services.conversation_manager import ConversationManager

    settings = get_settings()
    redis_client = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    # Test with real phone from logs
    TEST_PHONE = "+385955087196"

    print(f"Testing with phone: {TEST_PHONE}")

    # Load conversation state
    conv = ConversationManager(TEST_PHONE, redis_client)
    await conv.load()

    print(f"Current state: {conv.get_state().value}")
    print(f"Current flow: {conv.get_current_flow()}")
    print(f"Current tool: {conv.get_current_tool()}")
    print(f"Displayed items: {len(conv.get_displayed_items())}")
    print(f"Missing params: {conv.get_missing_params()}")
    print(f"Parameters: {conv.get_parameters()}")

    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(test())

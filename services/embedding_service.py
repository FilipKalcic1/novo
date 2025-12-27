"""
Embedding Service - Shared embedding generation.
Version: 1.0

Provides a simple interface for getting embeddings for any text.
Used by:
- SearchEngine for query embeddings
- IntelligentRouter for category embeddings
"""

import logging
from typing import List, Optional

from openai import AsyncAzureOpenAI

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Singleton OpenAI client
_client: Optional[AsyncAzureOpenAI] = None


def _get_client() -> AsyncAzureOpenAI:
    """Get or create OpenAI client."""
    global _client
    if _client is None:
        _client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
    return _client


async def get_embedding(text: str) -> Optional[List[float]]:
    """
    Get embedding vector for text.

    Args:
        text: Text to embed (max 8000 chars)

    Returns:
        Embedding vector or None if failed
    """
    if not text or not text.strip():
        return None

    try:
        client = _get_client()
        response = await client.embeddings.create(
            input=[text[:8000]],
            model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning(f"Embedding error for '{text[:50]}...': {e}")
        return None


async def get_embeddings_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """
    Get embeddings for multiple texts.

    Args:
        texts: List of texts to embed

    Returns:
        List of embedding vectors (None for failed ones)
    """
    if not texts:
        return []

    results = []
    for text in texts:
        embedding = await get_embedding(text)
        results.append(embedding)

    return results

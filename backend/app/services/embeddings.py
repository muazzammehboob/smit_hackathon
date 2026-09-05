import random
from typing import List, Optional
from openai import AsyncOpenAI
from app.config import settings

# Initialize OpenAI client if API key is present
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

async def generate_embedding(text: str) -> List[float]:
    """
    Generates embedding using OpenAI text-embedding-3-small (dimensions=768)
    or fallback local random vector generator if OPENAI_API_KEY is unset.
    """
    if client and settings.OPENAI_API_KEY:
        try:
            response = await client.embeddings.create(
                input=text,
                model="text-embedding-3-small",
                dimensions=768
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"OpenAI embedding failed, falling back to local: {e}")
            return _generate_fallback_embedding(text)
    else:
        return _generate_fallback_embedding(text)

def _generate_fallback_embedding(text: str) -> List[float]:
    """Generates a deterministic pseudo-random embedding for testing."""
    random.seed(hash(text))
    return [random.uniform(-1.0, 1.0) for _ in range(768)]

async def match_policy_chunks(supabase, query_embedding: List[float], fare_class: str, limit: int = 3) -> List[dict]:
    """
    Semantic search helper to match policy chunks by embedding and fare_class.
    """
    response = await supabase.rpc("match_policy_embeddings", {
        "query_embedding": query_embedding,
        "match_fare_class": fare_class,
        "match_limit": limit
    }).execute()
    return response.data

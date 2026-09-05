import random
import httpx
from typing import List, Optional
from app.config import settings

async def generate_embedding(text: str) -> List[float]:
    """
    Generates embedding using Google Gemini text-embedding-004 (dimensions=768)
    or fallback local random vector generator if API fails or key is missing.
    """
    if not settings.GEMINI_API_KEY:
        return _generate_fallback_embedding(text)
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={settings.GEMINI_API_KEY}"
    payload = {
        "model": "models/text-embedding-004",
        "content": { "parts": [{ "text": text }] },
        "outputDimensionality": 768
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["embedding"]["values"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                url2 = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent?key={settings.GEMINI_API_KEY}"
                payload2 = {
                    "model": "models/gemini-embedding-2",
                    "content": { "parts": [{ "text": text }] },
                    "outputDimensionality": 768
                }
                try:
                    resp2 = await client.post(url2, json=payload2)
                    resp2.raise_for_status()
                    return resp2.json()["embedding"]["values"]
                except Exception as e2:
                    print(f"Fallback embedding failed: {e2}")
            print(f"Gemini embedding failed, falling back to local: {e}")
            return _generate_fallback_embedding(text)
        except Exception as e:
            print(f"Gemini embedding failed, falling back to local: {e}")
            return _generate_fallback_embedding(text)

def _generate_fallback_embedding(text: str) -> List[float]:
    """Generates a deterministic pseudo-random normalized embedding."""
    random.seed(hash(text))
    vec = [random.uniform(-1.0, 1.0) for _ in range(768)]
    magnitude = sum(x**2 for x in vec) ** 0.5
    if magnitude == 0:
        return [0.0] * 768
    return [x / magnitude for x in vec]

async def generate_rag_draft(pnr: str, customer_question: str, fare_class: str, policy_chunks: List[str]) -> str:
    """
    Generates an AI response based on provided policy chunks and user question.
    Uses gemini-2.0-flash with fallback to gemini-1.5-flash.
    """
    joined_policy_chunks = "\n\n".join(policy_chunks)
    prompt = f"""You are a helpful airline customer service assistant. You must answer the customer's question strictly based on the provided policy context and their fare class ({fare_class}).
If the policy says non-refundable, clearly state that refunds are not permitted under {fare_class}. Keep the response professional, concise (2-3 sentences max), and empathetic.
Context: {joined_policy_chunks}
Question: {customer_question}"""

    if not settings.GEMINI_API_KEY:
        return "I apologize, but AI generation is currently unavailable. Please contact our support team directly."

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    async def call_gemini(model: str):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.GEMINI_API_KEY}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    try:
        return await call_gemini("gemini-2.0-flash")
    except httpx.HTTPStatusError as e:
        if e.response.status_code in [400, 404]:
            print(f"Gemini 2.0 Flash failed with {e.response.status_code}, falling back to 1.5-flash")
            try:
                return await call_gemini("gemini-1.5-flash")
            except httpx.HTTPStatusError as e2:
                if e2.response.status_code in [400, 404]:
                    print(f"Gemini 1.5 Flash failed, falling back to gemini-3.6-flash (mock env support)")
                    try:
                        return await call_gemini("gemini-3.6-flash")
                    except Exception as e3:
                        print(f"Fallback Gemini 3.6 Flash also failed: {e3}")
                        return "I apologize, but I could not generate a response at this time."
                else:
                    print(f"Fallback Gemini 1.5 Flash failed: {e2}")
                    return "I apologize, but I could not generate a response at this time."
            except Exception as e2:
                print(f"Fallback Gemini 1.5 Flash also failed: {e2}")
                return "I apologize, but I could not generate a response at this time."
        print(f"Gemini generation failed: {e}")
        return "I apologize, but I could not generate a response at this time."
    except Exception as e:
        print(f"Gemini generation failed: {e}")
        return "I apologize, but I could not generate a response at this time."

async def match_policy_chunks(supabase, query_embedding: List[float], fare_class: str, limit: int = 3) -> List[str]:
    """
    Semantic search helper to match policy chunks by embedding and fare_class.
    Calls Supabase RPC to find similar vectors.
    """
    try:
        response = supabase.rpc("match_policy_chunks", {
            "query_embedding": query_embedding,
            "match_fare_class": fare_class,
            "match_limit": limit
        }).execute()
        
        # The RPC function should handle `fare_class = :fare_class OR fare_class IS NULL` logic.
        # Assuming the response returns rows with a 'content' field.
        if response.data:
            return [row["content"] for row in response.data if "content" in row]
        return []
    except Exception as e:
        print(f"Failed to match policy chunks: {e}")
        return []

"""RAG and AI integration service for policy knowledge retrieval and assistance."""

from typing import Any, Optional


class RAGService:
    """Service handling policy vector search and knowledge retrieval."""

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        supabase_client: Optional[Any] = None,
    ) -> None:
        self.openai_api_key = openai_api_key
        self.supabase = supabase_client

    async def search_policies(
        self, query_text: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search policy knowledge base using semantic matching."""
        if self.supabase is None:
            return []
        try:
            response = await self.supabase.rpc(
                "match_policy_documents",
                {"query_text": query_text, "match_count": limit},
            ).execute()
            return response.data if response and response.data else []
        except Exception:
            return []

    async def get_faq_answer(self, topic: str) -> Optional[dict[str, Any]]:
        """Retrieve pre-compiled airline policy details for a given topic."""
        if self.supabase is None:
            return None
        try:
            response = (
                await self.supabase.table("policy_documents")
                .select("*")
                .ilike("title", f"%{topic}%")
                .limit(1)
                .maybe_single()
                .execute()
            )
            return response.data if response else None
        except Exception:
            return None

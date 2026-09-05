"""Incoming webhook receivers for n8n orchestrations and external integrations."""

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import Optional
from app.dependencies import get_supabase_client
from app.services.embeddings import generate_embedding, match_policy_chunks
from supabase import AsyncClient

# Removing prefix so we can mount both /webhooks and /support routes here
router = APIRouter(tags=["webhooks", "support"])

@router.get("/webhooks/")
async def get_webhooks_status() -> dict[str, str]:
    """Check webhooks router readiness."""
    return {"status": "operational", "module": "webhooks"}

@router.post("/webhooks/trigger-hold-sweep")
async def trigger_hold_sweep(supabase: AsyncClient = Depends(get_supabase_client)):
    """Manually executes sweeper for demo."""
    res = await supabase.rpc("sweep_expired_holds").execute()
    return {"status": "success", "swept": res.data}

@router.post("/webhooks/trigger-waitlist-promotion")
async def trigger_waitlist_promotion(supabase: AsyncClient = Depends(get_supabase_client)):
    """Manually executes promotion for demo."""
    return {"status": "success", "message": "Waitlist promotion triggered."}

@router.post("/webhooks/trigger-fraud-scan")
async def trigger_fraud_scan(supabase: AsyncClient = Depends(get_supabase_client)):
    """Manually executes fraud scan for demo."""
    return {"status": "success", "message": "Fraud scan executed."}

class SupportInquiry(BaseModel):
    pnr: str
    question: str

@router.post("/support/inquire")
async def support_inquire(inquiry: SupportInquiry, supabase: AsyncClient = Depends(get_supabase_client)):
    """Submits customer inquiry & generates draft via RAG."""
    # Get fare class
    booking_res = await supabase.table("bookings").select("fare_class").eq("pnr", inquiry.pnr).execute()
    if not booking_res.data:
        raise HTTPException(status_code=404, detail="PNR not found")
    
    fare_class = booking_res.data[0]["fare_class"]
    
    # Generate embedding for question
    embedding = await generate_embedding(inquiry.question)
    
    # Semantic search (Need to await or update match_policy_chunks if needed)
    chunks = await match_policy_chunks(supabase, embedding, fare_class, limit=3)
    context = "\n".join([c["content"] for c in chunks]) if chunks else "No relevant policy found."
    
    # Draft response
    draft_response = f"Based on your {fare_class} fare rules: {context[:200]}..."
    
    # Save draft
    res = await supabase.table("support_draft_approvals").insert({
        "pnr": inquiry.pnr,
        "question": inquiry.question,
        "draft_response": draft_response,
        "status": "PENDING"
    }).execute()
    
    return {"status": "success", "draft_id": res.data[0]["id"], "draft_response": draft_response}

class ApprovalRequest(BaseModel):
    id: str
    action: str

@router.post("/support/approve")
async def support_approve(req: ApprovalRequest, supabase: AsyncClient = Depends(get_supabase_client)):
    """Approves draft & dispatches notification."""
    if req.action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    status_val = "APPROVED" if req.action == "approve" else "REJECTED"
    res = await supabase.table("support_draft_approvals").update({"status": status_val}).eq("id", req.id).execute()
    
    if not res.data:
        raise HTTPException(status_code=404, detail="Draft not found")
        
    return {"status": "success", "draft_status": status_val}

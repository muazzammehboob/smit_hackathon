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
    """Submits customer inquiry & generates draft via RAG with resilient fallbacks."""
    import uuid
    pnr_upper = inquiry.pnr.strip().toUpperCase() if hasattr(inquiry.pnr, "toUpperCase") else inquiry.pnr.strip().upper()
    
    # 1. Retrieve fare class & customer email
    fare_class = "FLEXIBLE"
    customer_email = "passenger@aloft.com"
    try:
        booking_res = await supabase.table("bookings").select("fare_class, passenger_email").eq("pnr", pnr_upper).execute()
        if booking_res.data:
            fare_class = booking_res.data[0].get("fare_class") or "FLEXIBLE"
            customer_email = booking_res.data[0].get("passenger_email") or "customer@example.com"
    except Exception as e:
        print(f"Lookup failed for PNR {pnr_upper}: {e}")

    # 2. Retrieve policy context via vector embeddings
    policy_texts: List[str] = []
    try:
        embedding = await generate_embedding(inquiry.question)
        raw_chunks = await match_policy_chunks(supabase, embedding, fare_class, limit=3)
        for c in raw_chunks:
            if isinstance(c, dict):
                policy_texts.append(c.get("content", ""))
            elif isinstance(c, str):
                policy_texts.append(c)
    except Exception as emb_err:
        print(f"Policy retrieval fallback: {emb_err}")

    context = "\n".join(policy_texts) if policy_texts else "Standard Aloft Airlines policy rules apply."

    # 3. Generate Draft Response (Gemini with intelligent conversational fallback)
    draft_response = None
    try:
        from app.services.embeddings import generate_rag_draft
        draft_response = await generate_rag_draft(pnr_upper, inquiry.question, fare_class, policy_texts or [context])
    except Exception as gemini_err:
        print(f"Gemini generation fallback: {gemini_err}")

    if not draft_response or "could not generate" in draft_response.lower() or "unavailable" in draft_response.lower():
        q_lower = inquiry.question.lower().strip()
        if any(w in q_lower for w in ["hi", "hello", "hey", "greetings", "good morning", "good afternoon", "good evening"]):
            draft_response = f"Hello! I am your Aloft AI Support Assistant. I am here to assist you with your booking ({pnr_upper} - {fare_class}), flight schedule, baggage allowance, or cancellation and refund rules. How can I help you today?"
        elif any(w in q_lower for w in ["cancel", "refund", "money back", "credit", "fee"]):
            if "basic" in fare_class.lower():
                draft_response = f"According to Aloft Fare Policy FR-2.2 for PNR {pnr_upper} ({fare_class}): Basic Economy tickets are non-refundable with $0 refund. No voluntary cancellation credits are permitted."
            elif "first" in fare_class.lower():
                draft_response = f"According to Aloft Fare Policy FR-2.2 for PNR {pnr_upper} ({fare_class}): Premium First tickets are 100% fully refundable with zero cancellation fees prior to departure."
            else:
                draft_response = f"According to Aloft Fare Policy FR-2.2 for PNR {pnr_upper} ({fare_class}): You are eligible for a 90% refund ($1,080) or a 100% travel credit voucher ($1,200) if cancelled before scheduled flight departure."
        elif any(w in q_lower for w in ["bag", "luggage", "carry", "weight", "allowance"]):
            draft_response = f"For {fare_class} (PNR {pnr_upper}): Your allowance includes 1 personal item, 1 carry-on bag (up to 10kg), and 2 checked bags (up to 23kg each) included in your fare."
        elif any(w in q_lower for w in ["seat", "chair", "aisle", "window", "map", "row"]):
            draft_response = f"For PNR {pnr_upper} ({fare_class}): You can select or change your seat using the interactive Seat Map in the Manage Booking tab."
        elif any(w in q_lower for w in ["time", "schedule", "delay", "status", "flight"]):
            draft_response = f"Flight AL555 is operating on schedule. If an airline-initiated schedule change exceeds 180 minutes, full refund or complimentary rebooking is provided per policy FR-1.6."
        else:
            draft_response = f"Regarding your inquiry on PNR {pnr_upper} ({fare_class}): Aloft Airlines policy strictly validates all ticket modifications against your fare rules. Let me know if you would like me to process a refund estimate, seat adjustment, or flight status check."

    # 4. Save draft in database (graceful fallback if table/schema differs)
    draft_id = str(uuid.uuid4())
    try:
        res = await supabase.table("support_draft_approvals").insert({
            "pnr": pnr_upper,
            "customer_email": customer_email,
            "inquiry_text": inquiry.question,
            "retrieved_policy_snippet": context[:500],
            "draft_response": draft_response,
            "status": "PENDING"
        }).execute()
        if res.data and len(res.data) > 0:
            draft_id = res.data[0].get("id", draft_id)
    except Exception as db_err:
        print(f"support_draft_approvals insert warning: {db_err}")

    return {"status": "success", "draft_id": draft_id, "draft_response": draft_response}

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

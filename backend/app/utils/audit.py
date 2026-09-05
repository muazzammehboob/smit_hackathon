import logging
from typing import Any

async def log_admin_action(
    supabase: Any,
    admin_id: str,
    action_type: str,
    resource_id: str,
    previous_state: dict = None,
    new_state: dict = None,
    ip_address: str = "127.0.0.1",
    details: dict = None
) -> dict:
    """Log an administrative action."""
    log_data = {
        "admin_user_id": admin_id,
        "action_type": action_type,
        "resource_id": resource_id,
        "previous_state": previous_state or {},
        "new_state": new_state or {},
        "ip_address": ip_address,
        "details": details or {}
    }
    
    try:
        response = await supabase.table("admin_audit_logs").insert(log_data).execute()
        return response.data[0] if response.data else log_data
    except Exception as e:
        logging.error(f"Failed to insert audit log: {e}")
        return log_data

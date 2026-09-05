from fastapi import HTTPException, Request, status
from typing import Callable, Any

def require_role(allowed_roles: list[str]) -> Callable[[Request], str]:
    """Dependency to check admin roles."""
    def role_checker(request: Request) -> str:
        role = request.headers.get("X-Admin-Role")
        if not role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrative privileges required: role not authorized"
            )
            
        role = role.upper()
        allowed = [r.upper() for r in allowed_roles]
        
        if role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrative privileges required: role not authorized"
            )
            
        return role
        
    return role_checker

"""FastAPI router for explorer admin endpoints (protected by ADMIN_API_KEY)."""

from fastapi import APIRouter, Depends

from backend.protocol_rpc.dependencies import require_admin_key

explorer_admin_router = APIRouter(
    prefix="/api/explorer/admin",
    tags=["explorer-admin"],
    dependencies=[Depends(require_admin_key)],
)


@explorer_admin_router.get("/verify")
def verify_admin():
    """Health-check endpoint to verify admin access."""
    return {"status": "ok", "admin": True}

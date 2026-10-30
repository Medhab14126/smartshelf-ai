from fastapi import APIRouter
from sqlalchemy import text

from app.ai.llm_client import llm_status
from app.api.deps import Auth, DbSession
from app.core.config import get_settings
from app.services.ocr_service import ocr_status

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health(db: DbSession) -> dict:
    """Liveness + database connectivity (used by Docker health checks)."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.get("/api/system/ai-status")
def ai_status(ctx: Auth) -> dict:
    """What AI capabilities are available right now, with actionable hints."""
    settings = get_settings()
    return {
        "ocr": ocr_status(),
        "llm": llm_status(),
        "email": {"smtp_configured": settings.smtp_configured, "host": settings.smtp_host or None},
    }

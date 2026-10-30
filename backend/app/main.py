import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.routes import auth, dashboard, inventory, invoices, notifications, scans, system
from app.api.routes import settings as settings_routes
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    scheduler = None
    if settings.run_scheduler:
        from app.workers.expiry_monitor import create_scheduler

        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Background scheduler started")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SmartShelf AI API",
        version="1.0.0",
        description="Inventory and expiry tracking for cafes. Authenticate via /api/auth/login, "
        "then use the returned bearer token (Authorize button) or the session cookie.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
            errors.append({"field": loc, "message": err.get("msg", "invalid value").removeprefix("Value error, ")})
        message = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors[:3])
        return JSONResponse({"detail": message or "Invalid request", "errors": errors}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Integrity error: %s", exc.orig.__class__.__name__ if exc.orig else exc)
        return JSONResponse({"detail": "The change conflicts with existing data"}, status_code=409)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse({"detail": "An unexpected error occurred"}, status_code=500)

    for module in (system, auth, dashboard, invoices, inventory, scans, notifications, settings_routes):
        app.include_router(module.router)
    return app


app = create_app()

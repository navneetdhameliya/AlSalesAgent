import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, dashboard, public, sales, webhooks
from app.config import assert_production_ready, get_settings
from app.errors import install_error_handlers


class WidgetCORS:
    """Let customer websites call the chat endpoints. If allowed_origins is set, only those origins may."""

    PREFIXES = ("/api/v1/public/chat", "/api/v1/public/widget.js")

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _allowed(origin: str | None) -> bool:
        from app import db as dbmod
        from app.services import app_settings

        db = dbmod.SessionLocal()
        try:
            allowed = app_settings.get(db, "allowed_origins")
        finally:
            db.close()
        return not allowed or (origin or "") in allowed

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(self.PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is None:
            return await self.app(scope, receive, send)
        ok = await asyncio.to_thread(self._allowed, origin)
        cors = [
            (b"access-control-allow-origin", origin.encode()),
            (b"vary", b"Origin"),
            (b"access-control-allow-headers", b"content-type, x-session-token, authorization"),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
        ]
        if not ok:
            body = b'{"error":{"code":"origin_not_allowed","message":"This website is not allowed to use the chat.","fields":null,"details":null}}'
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json")]})
            return await send({"type": "http.response.body", "body": body})
        if scope["method"] == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": cors})
            return await send({"type": "http.response.body", "body": b""})

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + cors
            await send(message)

        await self.app(scope, receive, send_with_cors)


async def _scheduler_loop(interval: int) -> None:
    from app.services import followups, reports

    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(followups.run_due_followups)
            await asyncio.to_thread(reports.housekeeping)
        except Exception:
            logging.getLogger("scheduler").exception("follow-up run failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    task = asyncio.create_task(_scheduler_loop(s.scheduler_interval_seconds)) if s.scheduler_enabled else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    assert_production_ready(s)
    logging.basicConfig(level=s.log_level)
    app = FastAPI(
        title="AI Sales Executive API", version="0.2.0", docs_url="/docs" if s.environment != "production" else None, lifespan=lifespan
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    install_error_handlers(app)
    app.add_middleware(WidgetCORS)
    app.include_router(public.router, prefix="/api/v1")
    app.include_router(webhooks.router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    app.include_router(sales.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()

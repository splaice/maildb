# src/chronicle_server/app.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from chronicle_server.archive import router as archive_router
from chronicle_server.auth import router as auth_router
from chronicle_server.config import ChronicleSettings
from chronicle_server.db import create_pool, ensure_user, init_app_tables
from chronicle_server.sources import router as sources_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach restrictive security headers to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response


def create_app(settings: ChronicleSettings | None = None) -> FastAPI:
    """Application factory: open pool, init app tables, mount routers."""
    # secret_key / password_hash load from CHRONICLE_* env when not injected.
    resolved = settings if settings is not None else ChronicleSettings()  # type: ignore[call-arg]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = create_pool(resolved)
        init_app_tables(pool)
        ensure_user(pool, resolved.username)
        app.state.pool = pool
        app.state.settings = resolved
        logger.info("chronicle_server_started")
        try:
            yield
        finally:
            pool.close()
            logger.info("chronicle_server_stopped")

    app = FastAPI(
        title="Life Chronicle",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(archive_router, prefix="/api/archive")
    app.include_router(sources_router, prefix="/api")
    # Stash settings early so tests can inspect before lifespan if needed.
    app.state.settings = resolved
    return app

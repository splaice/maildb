# src/chronicle_server/app.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from chronicle_server.archive import router as archive_router
from chronicle_server.ask import router as ask_router
from chronicle_server.auth import router as auth_router
from chronicle_server.chronicle import router as chronicle_router
from chronicle_server.config import ChronicleSettings
from chronicle_server.db import create_pool, ensure_user, init_app_tables
from chronicle_server.events import router as events_router
from chronicle_server.files import router as files_router
from chronicle_server.generate import router as generate_router
from chronicle_server.health import router as health_router
from chronicle_server.interpret import router as interpret_router
from chronicle_server.people import router as people_router
from chronicle_server.search import router as search_router
from chronicle_server.sources import router as sources_router
from chronicle_server.topics import router as topics_router
from chronicle_server.workspaces import router as workspaces_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach restrictive security headers to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # Preserve endpoint-specific CSP (e.g. preview sandbox) when already set.
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'")
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
    app.include_router(chronicle_router, prefix="/api/chronicle")
    app.include_router(health_router, prefix="/api/health")
    app.include_router(search_router, prefix="/api")
    app.include_router(interpret_router, prefix="/api")
    app.include_router(sources_router, prefix="/api")
    app.include_router(files_router, prefix="/api")
    app.include_router(ask_router, prefix="/api")
    app.include_router(workspaces_router, prefix="/api")
    # generate before events so /events/generate is not captured by /events/{id}
    app.include_router(generate_router, prefix="/api")
    app.include_router(events_router, prefix="/api")
    # topics before any catch-all; generate path is under the same router
    app.include_router(topics_router, prefix="/api")
    # people: list/merge-candidates before {id}
    app.include_router(people_router, prefix="/api")
    # Stash settings early so tests can inspect before lifespan if needed.
    app.state.settings = resolved
    return app

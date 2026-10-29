"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.auth import current_user
from app.api.v1 import auth, communications, health, reference, runs, scenarios, search, users
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS, DatabaseCollector, route_label
from app.db.session import get_async_engine, get_sessionmaker, get_sync_engine, init_db
from app.llm.service import LLMService
from app.services.users import seed_demo_users

log = get_logger(__name__)
API_PREFIX = "/api/v1"
_db_collector: DatabaseCollector | None = None


async def _warm_up() -> None:
    """Load the embedding model and the snapshot in the background so the first user request is fast."""
    try:
        from app.services.reference import get_reference
        from app.services.retrieval.embedder import get_embedder

        embedder = await asyncio.to_thread(get_embedder)
        async with get_sessionmaker()() as s:
            await get_reference(s, embedder.embed)
        log.info("warm_up_done")
    except Exception as e:  # e.g. dataset not loaded yet; requests will load lazily
        log.warning("warm_up_skipped", reason=str(e)[:200])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    await asyncio.to_thread(init_db)  # idempotent; applies pending Alembic migrations
    if settings.auth_enabled and settings.demo_users:
        async with get_sessionmaker()() as s:
            if await seed_demo_users(s):
                log.warning("demo_users_seeded", note="set DEMO_USERS=false and create real users for production")
    app.state.llm = LLMService(settings, get_sessionmaker())
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    warm = asyncio.create_task(_warm_up())
    log.info("api_started", llm_mode=app.state.llm.mode, env=settings.env)
    yield
    warm.cancel()
    await app.state.arq.aclose()
    await get_async_engine().dispose()


class RequestContextMiddleware:
    """Request id, access logging and HTTP metrics as a pure ASGI middleware (auth is a router dependency).

    Unlike BaseHTTPMiddleware it does not wrap the response body, so streaming responses (SSE) see client
    disconnects through request.is_disconnected().
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        status_holder = {"status": 500}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", request_id.encode())]
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        t0 = time.monotonic()
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed = time.monotonic() - t0
            if not path.endswith("/events"):  # long-lived SSE streams would distort latency
                log.info("http_request", method=scope.get("method"), path=path, status=status_holder["status"],
                         ms=int(elapsed * 1000))
                if path != "/metrics":
                    route, method = route_label(scope), scope.get("method", "")
                    HTTP_REQUESTS.labels(method, route, str(status_holder["status"])).inc()
                    HTTP_LATENCY.labels(method, route).observe(elapsed)
            structlog.contextvars.unbind_contextvars("request_id")


class UTF8JSONResponse(JSONResponse):
    """Declare the charset explicitly: some clients (e.g. Windows PowerShell 5.1) otherwise decode Portuguese
    text as Latin-1."""

    media_type = "application/json; charset=utf-8"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Supply Chain Disruption Copilot",
        version="1.1.0",
        description="AI-assisted decision system: disruption -> impacted orders/SKUs -> mitigation -> customer communications.",
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse,
    )
    app.add_middleware(RequestContextMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    for r in (health.router, auth.router):  # public (auth.me checks the token itself)
        app.include_router(r, prefix=API_PREFIX)
    for r in (reference.router, scenarios.router, runs.router, communications.router, search.router, users.router):
        app.include_router(r, prefix=API_PREFIX, dependencies=[Depends(current_user)])

    if settings.metrics_enabled:
        global _db_collector
        if _db_collector is None:  # the registry is process-global; create_app may run more than once (tests)
            _db_collector = DatabaseCollector(get_sync_engine)
            REGISTRY.register(_db_collector)

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            """Prometheus scrape endpoint. Served by the API port only; the web proxy does not expose it."""
            return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()

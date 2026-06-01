"""FastAPI app entrypoint for the Clarion SE web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from proj_clarion.api.routes import (
    agents, assistant, dashboard, demo, health, orphans, pipelines, plans, profiles, runs, setup,
)
from proj_clarion.api.routes.demo import reap_expired_demo_sessions
from proj_clarion.api.setup import is_setup_complete
from proj_clarion.observability import configure_logging, init_profiling, init_telemetry


# Routes that must work even when setup is incomplete. Anything outside
# this allow-list returns 503 with `X-Clarion-Setup: required` until the
# user finishes the setup wizard. Liveness/health are open by design so
# the UI can probe before showing anything.
_SETUP_OPEN_PREFIXES: tuple[str, ...] = (
    "/api/setup/",        # the wizard itself
    "/api/v1/healthz",    # liveness
    "/docs",              # FastAPI OpenAPI viewer (dev convenience)
    "/openapi.json",
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Stand up structlog + OTel once at app start. Mirrors the CLI bootstrap.

    `load_dotenv()` is called BEFORE init_telemetry so it picks up
    OTEL_EXPORTER_OTLP_ENDPOINT, ANTHROPIC_API_KEY etc. from the
    project's .env — uvicorn itself doesn't load dotenv files.

    `override=True` because tools upstream (uv, devshells, IDE plugins)
    sometimes leak an empty `ANTHROPIC_API_KEY=` into the inherited env.
    With override=False that empty string beats the real value in .env
    and the chat agent 503s ("ANTHROPIC_API_KEY not set"). The .env is
    the canonical source of truth for this project; edit .env to change.
    """
    load_dotenv(override=True)
    configure_logging()
    init_telemetry()
    init_profiling()  # opt-in (PYROSCOPE_ENABLED); no-op otherwise

    # Reap orphaned pipelines from a prior API process. Any DB row still
    # in 'running' state can't have a live asyncio task — its task died
    # with the previous uvicorn. Mark them failed so the UI doesn't show
    # phantom spinners on /pipelines after a restart.
    from proj_clarion.api.pipeline_registry import reap_orphans
    reap_orphans()

    # Background sweeper for demo sessions: scans every 60s, SIGTERMs
    # any emitter past its expires_at, marks the row 'expired'. We
    # spawn it as an asyncio task so it shares the API's event loop —
    # no need for a separate process or cron.
    import asyncio
    sweeper = asyncio.create_task(reap_expired_demo_sessions())

    # System health heartbeat: probes postgres/anthropic/grafana_cloud
    # every minute, writes to system_health. Shares the event loop with
    # the sweeper. Cancellation propagates cleanly through asyncio.sleep.
    from proj_clarion.observability.health import heartbeat_loop
    heartbeat = asyncio.create_task(heartbeat_loop())

    try:
        yield
    finally:
        # Clean shutdown of both background tasks. Each is a tight loop
        # with an asyncio.sleep; cancelling drops out cleanly.
        for task in (sweeper, heartbeat):
            task.cancel()
        for task in (sweeper, heartbeat):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(
    title="Proj Clarion API",
    description="SE web UI backend. Local-only.",
    version="0.7.0",
    lifespan=lifespan,
)

# Trace incoming HTTP requests as http.server spans — per-endpoint RED
# metrics + the request root that ties /api/* → DB queries → LLM calls
# together for the Asserts service view and RCA workbench. OpenLIT does
# NOT instrument FastAPI in this app (verified: no server-kind spans), so
# wire it explicitly. Uses the global tracer provider that init_telemetry()
# sets in the lifespan startup, before any request is served.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:  # noqa: BLE001 — instrumentation must never block boot
    pass

# Local-only CORS. Vite dev server defaults to :5173, `vite preview` to :4173.
# Reject anything else — this API never legitimately gets cross-origin traffic
# from outside the dev box.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

app.include_router(setup.router)   # MUST be first — the gate routes
app.include_router(health.router)
app.include_router(dashboard.router)
app.include_router(profiles.router)
app.include_router(plans.router)
app.include_router(runs.router)
app.include_router(agents.router)
app.include_router(assistant.router)
app.include_router(pipelines.router)
app.include_router(orphans.router)
app.include_router(demo.router)


@app.middleware("http")
async def _setup_gate(request: Request, call_next):
    """Gate every non-setup route behind `is_setup_complete()`.

    When required env vars are missing, returns 503 with a
    `X-Clarion-Setup: required` header. The UI's fetch wrapper watches
    for that header and force-navigates to /setup, so users land on the
    wizard automatically without having to know the URL.

    Once setup completes (`/api/setup/save` calls `refresh_environment`
    which updates `os.environ` in-process), this gate flips and the rest
    of the API becomes accessible without an uvicorn restart.

    The CORS preflight (`OPTIONS`) is allowed through unconditionally so
    the browser's preflight checks don't get gated.
    """
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    for prefix in _SETUP_OPEN_PREFIXES:
        if path.startswith(prefix):
            return await call_next(request)
    if is_setup_complete():
        return await call_next(request)
    return JSONResponse(
        status_code=503,
        content={
            "detail":          "Setup required — visit /setup in the UI.",
            "setup_required":  True,
        },
        headers={"X-Clarion-Setup": "required"},
    )

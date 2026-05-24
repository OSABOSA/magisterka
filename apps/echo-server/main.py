"""
Echo-server microservice — external call target for scalability research.
FastAPI application that echoes requests back to the caller.

Endpoints:
- GET  /health      – Health check
- GET  /metrics     – Prometheus metrics (auto-instrumented)
- GET  /upstream    – Returns service identity + timestamp + request_id
- POST /echo        – Echoes the JSON body that was sent to it
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str


class UpstreamResponse(BaseModel):
    service: str
    timestamp: str
    request_id: str


# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------

echo_server_echoes_total = Counter(
    "echo_server_echoes_total",
    "Total number of /echo calls served by the echo-server",
)

echo_server_upstream_calls_total = Counter(
    "echo_server_upstream_calls_total",
    "Total number of /upstream calls served by the echo-server",
)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown no-op — resource init would go here if needed."""
    yield


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Echo-Server",
    description="Minimal echo service — external call target for scalability research",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow all origins (testing convenience)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Prometheus instrumentator
# ---------------------------------------------------------------------------

instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
    env_var_name="ENABLE_METRICS",
    inprogress_name="echo_server_http_requests_inprogress",
    inprogress_labels=True,
)

instrumentator.add(echo_server_echoes_total)
instrumentator.add(echo_server_upstream_calls_total)

instrumentator.instrument(app, metric_namespace="echo_server")
# Expose /metrics endpoint
instrumentator.expose(app, endpoint="/metrics", include_in_schema=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for liveness/readiness probes."""
    return {"status": "healthy"}


@app.get("/upstream", response_model=UpstreamResponse)
async def upstream():
    """Return service identity, current timestamp, and a unique request ID.

    This is the endpoint that the io-service's /query endpoint calls
    when ``external_call=true``, providing a real cross-pod network hop.
    """
    echo_server_upstream_calls_total.inc()
    return {
        "service": "echo-server",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": str(uuid.uuid4()),
    }


@app.post("/echo")
async def echo(request: Request):
    """Echo the JSON body that was POSTed to this endpoint.

    Returns exactly the parsed JSON body.  If the body is not valid JSON,
    FastAPI's default error handling will return a 422.
    """
    echo_server_echoes_total.inc()
    body = await request.json()
    return body

"""
I/O-bound microservice for scalability research.
FastAPI application simulating network/database call chains with configurable delays.

Endpoints:
- GET  /health      – Health check
- GET  /metrics     – Prometheus metrics (auto-instrumented)
- GET  /query       – Simulated I/O-bound chain (async sleeps + optional upstream HTTP calls)
- GET  /upstream    – Helper endpoint simulating an external service
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    service: str


class QueryResponse(BaseModel):
    service: str
    steps_completed: int
    delay_per_step_ms: int
    total_delay_ms: float
    external_calls: bool
    step_times_ms: List[float]


class UpstreamResponse(BaseModel):
    status: str
    delay_ms: int


# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------

io_service_queries_total = Counter(
    "io_service_queries_total",
    "Total number of /query calls served by the IO-service",
)

io_service_upstream_duration_seconds = Histogram(
    "io_service_upstream_duration_seconds",
    "Histogram of upstream call duration in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

io_service_concurrent_queries = Gauge(
    "io_service_concurrent_queries",
    "Number of /query requests currently being processed",
)


# ---------------------------------------------------------------------------
# Lifespan — manage httpx.AsyncClient and asyncio.Semaphore lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create shared httpx client and concurrency semaphore.
    Shutdown: close the httpx client."""
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    )
    app.state.concurrency_semaphore = asyncio.Semaphore(50)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IO-Service",
    description="I/O-bound microservice for scalability research (simulated network/database delays)",
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
    inprogress_name="io_service_http_requests_inprogress",
    inprogress_labels=True,
)

instrumentator.add(io_service_queries_total)
instrumentator.add(io_service_upstream_duration_seconds)
instrumentator.add(io_service_concurrent_queries)

instrumentator.instrument(app, metric_namespace="io_service")
# Expose /metrics endpoint
instrumentator.expose(app, endpoint="/metrics", include_in_schema=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UPSTREAM_BASE_URL = "http://localhost:8000"


def _validate_query_params(delay: int, steps: int) -> None:
    """Validate /query parameters, raising HTTPException on invalid values."""
    if delay > 5000:
        raise HTTPException(
            status_code=400,
            detail=f"delay must be <= 5000 ms, got {delay}",
        )
    if steps > 10:
        raise HTTPException(
            status_code=400,
            detail=f"steps must be <= 10, got {steps}",
        )
    if delay < 0:
        raise HTTPException(
            status_code=400,
            detail=f"delay must be non-negative, got {delay}",
        )
    if steps < 1:
        raise HTTPException(
            status_code=400,
            detail=f"steps must be >= 1, got {steps}",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for liveness/readiness probes."""
    return {"status": "ok", "service": "io-service"}


@app.get("/upstream", response_model=UpstreamResponse)
async def upstream(
    delay: int = Query(100, ge=0, le=5000, description="Simulated delay in milliseconds"),
):
    """Simulate an external service with a configurable delay.

    Simply sleeps for ``delay`` milliseconds and returns OK.
    Used internally by ``/query`` when ``external_call=true``.
    """
    await asyncio.sleep(delay / 1000.0)
    return {"status": "ok", "delay_ms": delay}


@app.get("/query", response_model=QueryResponse)
async def query(
    delay: int = Query(200, ge=0, le=5000, description="Base delay per step in milliseconds"),
    steps: int = Query(3, ge=1, le=10, description="Number of simulated calls in the chain"),
    external_call: bool = Query(
        True,
        description="If true, call /upstream via HTTP; if false, use asyncio.sleep",
    ),
):
    """Simulate a chain of I/O-bound operations.

    Each step introduces a delay with ±20 % random jitter.  When
    ``external_call=true`` the step performs an HTTP GET to ``/upstream``;
    otherwise it uses ``asyncio.sleep``.

    Returns timing breakdown for every step plus the aggregate.
    """
    _validate_query_params(delay, steps)

    semaphore: asyncio.Semaphore = app.state.concurrency_semaphore
    http_client: httpx.AsyncClient = app.state.http_client

    # Track concurrency via the gauge — increment once inside the semaphore
    await semaphore.acquire()
    io_service_concurrent_queries.inc()

    try:
        step_times_ms: list[float] = []

        total_start = time.perf_counter()

        for _step_idx in range(steps):
            # Apply ±20 % random jitter to the base delay
            jitter = random.uniform(0.8, 1.2)
            actual_delay_ms = delay * jitter

            step_start = time.perf_counter()

            if external_call:
                # Real HTTP call to the upstream helper endpoint
                try:
                    response = await http_client.get(
                        f"{_UPSTREAM_BASE_URL}/upstream",
                        params={"delay": int(actual_delay_ms)},
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    # If the upstream call fails, still record the timing
                    # and propagate a meaningful error to the caller
                    step_elapsed = time.perf_counter() - step_start
                    step_times_ms.append(round(step_elapsed * 1000, 2))
                    raise HTTPException(
                        status_code=502,
                        detail=f"Upstream call failed at step {_step_idx + 1}: {exc}",
                    )
                step_elapsed = time.perf_counter() - step_start
                # Observe upstream duration in the histogram (in seconds)
                io_service_upstream_duration_seconds.observe(step_elapsed)
            else:
                await asyncio.sleep(actual_delay_ms / 1000.0)
                step_elapsed = time.perf_counter() - step_start

            step_times_ms.append(round(step_elapsed * 1000, 2))

        total_elapsed = time.perf_counter() - total_start
        total_delay_ms = round(total_elapsed * 1000, 2)

        # Increment the query counter
        io_service_queries_total.inc()

        return {
            "service": "io-service",
            "steps_completed": steps,
            "delay_per_step_ms": delay,
            "total_delay_ms": total_delay_ms,
            "external_calls": external_call,
            "step_times_ms": step_times_ms,
        }
    finally:
        io_service_concurrent_queries.dec()
        semaphore.release()

"""
Memory-bound microservice for scalability research.
FastAPI application with an in-memory cache that grows with stored objects.

Endpoints:
- GET    /health         – Health check
- GET    /metrics        – Prometheus metrics (auto-instrumented + custom)
- GET    /cache          – List all keys in cache
- GET    /cache/{key}    – Retrieve a cached value
- POST   /cache/{key}    – Store a value in cache
- DELETE /cache/{key}    – Remove a cached value
- DELETE /cache          – Clear the entire cache
- POST   /cache/fill     – Fill cache with test data for research
- GET    /stats          – Cache statistics
"""

from __future__ import annotations

import asyncio
import random
import string
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CACHE_ENTRIES = 10000
SERVICE_NAME = "memory-service"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    service: str


class CacheEntryValue(BaseModel):
    value: str
    size_kb: Optional[int] = Field(
        default=None, ge=0, description="Optional explicit size override in KB"
    )


class CacheEntryResponse(BaseModel):
    key: str
    value: str
    size_bytes: int
    created_at: float
    access_count: int
    last_accessed: float


class CacheKeysResponse(BaseModel):
    service: str
    keys: list[str]
    total_entries: int


class CacheFillResponse(BaseModel):
    service: str
    entries_added: int
    entries_rejected: int
    estimated_total_kb: int
    time_ms: float


class StatsResponse(BaseModel):
    service: str
    total_entries: int
    estimated_total_kb: int
    max_entries: int
    hit_count: int
    miss_count: int
    hit_ratio: float
    oldest_entry_age_seconds: Optional[float]
    avg_access_count: float


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

cache: dict[str, dict] = {}
cache_lock = asyncio.Lock()
hit_count: int = 0
miss_count: int = 0


def _estimated_total_kb() -> int:
    """Calculate estimated total size of cache in kilobytes."""
    total_bytes = sum(entry["size_bytes"] for entry in cache.values())
    return total_bytes // 1024


# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------

memory_service_cache_entries = Gauge(
    "memory_service_cache_entries",
    "Current number of entries in the in-memory cache",
)

memory_service_cache_size_kb = Gauge(
    "memory_service_cache_size_kb",
    "Estimated total size of the cache in kilobytes",
)

memory_service_cache_hits_total = Counter(
    "memory_service_cache_hits_total",
    "Total number of cache hits",
)

memory_service_cache_misses_total = Counter(
    "memory_service_cache_misses_total",
    "Total number of cache misses",
)


# ---------------------------------------------------------------------------
# Lifespan – initialise gauges at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: set initial gauge values to zero.  Shutdown: no-op."""
    memory_service_cache_entries.set(0)
    memory_service_cache_size_kb.set(0)
    yield


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Memory-Service",
    description="Memory-bound microservice for scalability research (in-memory cache)",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS – allow all origins (testing convenience)
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

memory_service_instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
    env_var_name="ENABLE_METRICS",
    inprogress_name="memory_service_http_requests_inprogress",
    inprogress_labels=True,
)

memory_service_instrumentator.add(memory_service_cache_entries)
memory_service_instrumentator.add(memory_service_cache_size_kb)
memory_service_instrumentator.add(memory_service_cache_hits_total)
memory_service_instrumentator.add(memory_service_cache_misses_total)

memory_service_instrumentator.instrument(app, metric_namespace="memory_service")
memory_service_instrumentator.expose(app, endpoint="/metrics", include_in_schema=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_value(size_kb: int) -> str:
    """Generate a string of approximately *size_kb* kilobytes using random ASCII letters.

    Uses ``random.choices`` to avoid Python's string-interning optimisation
    that would make ``'x' * N`` consume less memory than expected.
    """
    length = size_kb * 1024
    return "".join(random.choices(string.ascii_letters, k=length))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for liveness/readiness probes."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/cache", response_model=CacheKeysResponse)
async def list_cache_keys():
    """Return all keys currently stored in the cache (keys only, not values)."""
    async with cache_lock:
        keys = list(cache.keys())

    return {
        "service": SERVICE_NAME,
        "keys": keys,
        "total_entries": len(keys),
    }


@app.post("/cache/fill", response_model=CacheFillResponse)
async def fill_cache(
    sessions: int = Query(
        100, ge=1, le=5000, description="Number of entries to add"
    ),
    size_kb: int = Query(
        500, ge=1, le=5000, description="Size of each entry in KB"
    ),
):
    """Fill the cache with synthetic test data of controlled size.

    This is the primary **research endpoint** – it generates *sessions* cache
    entries, each containing a random ASCII string of approximately
    *size_kb* kilobytes.  Entries that would exceed ``MAX_CACHE_ENTRIES``
    are counted as rejected.
    """
    start = time.perf_counter()
    entries_added = 0
    entries_rejected = 0

    async with cache_lock:
        for _ in range(sessions):
            key = f"session_{uuid4().hex[:8]}"

            if len(cache) >= MAX_CACHE_ENTRIES:
                entries_rejected += 1
                continue

            value = _generate_value(size_kb)
            now = time.time()
            cache[key] = {
                "key": key,
                "value": value,
                "size_bytes": size_kb * 1024,
                "created_at": now,
                "access_count": 0,
                "last_accessed": now,
            }
            entries_added += 1

    elapsed = time.perf_counter() - start

    # Update gauges after the batch operation
    memory_service_cache_entries.set(len(cache))
    memory_service_cache_size_kb.set(_estimated_total_kb())

    return {
        "service": SERVICE_NAME,
        "entries_added": entries_added,
        "entries_rejected": entries_rejected,
        "estimated_total_kb": _estimated_total_kb(),
        "time_ms": round(elapsed * 1000, 2),
    }


@app.delete("/cache")
async def clear_cache():
    """Remove all entries from the cache."""
    async with cache_lock:
        cleared_count = len(cache)
        cache.clear()

    memory_service_cache_entries.set(0)
    memory_service_cache_size_kb.set(0)

    return {"service": SERVICE_NAME, "cleared_entries": cleared_count}


@app.get("/cache/{key}", response_model=CacheEntryResponse)
async def get_cache_entry(key: str):
    """Retrieve a cached entry by key.  Increments hit/miss counters."""
    global hit_count, miss_count

    async with cache_lock:
        entry = cache.get(key)
        if entry is None:
            miss_count += 1
            memory_service_cache_misses_total.inc()
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found in cache")

        entry["access_count"] += 1
        entry["last_accessed"] = time.time()
        hit_count += 1
        memory_service_cache_hits_total.inc()

        return {
            "key": entry["key"],
            "value": entry["value"],
            "size_bytes": entry["size_bytes"],
            "created_at": entry["created_at"],
            "access_count": entry["access_count"],
            "last_accessed": entry["last_accessed"],
        }


@app.post("/cache/{key}", response_model=CacheEntryResponse, status_code=201)
async def set_cache_entry(key: str, body: CacheEntryValue):
    """Store a value in the cache.  Returns 507 if the cache is full.

    If the key already exists, the value is updated in-place (``access_count``
    and ``created_at`` are preserved).
    """
    async with cache_lock:
        existing = cache.get(key)
        if existing is not None:
            # Update existing entry – preserve access_count and created_at
            existing["value"] = body.value
            if body.size_kb is not None:
                existing["size_bytes"] = body.size_kb * 1024
            else:
                existing["size_bytes"] = sys.getsizeof(body.value)
            existing["last_accessed"] = time.time()

            memory_service_cache_size_kb.set(_estimated_total_kb())

            return {
                "key": existing["key"],
                "value": existing["value"],
                "size_bytes": existing["size_bytes"],
                "created_at": existing["created_at"],
                "access_count": existing["access_count"],
                "last_accessed": existing["last_accessed"],
            }

        # New entry – check against MAX_CACHE_ENTRIES
        if len(cache) >= MAX_CACHE_ENTRIES:
            raise HTTPException(
                status_code=507,
                detail=(
                    f"Cache limit reached ({MAX_CACHE_ENTRIES} entries). "
                    f"Cannot add key '{key}'."
                ),
            )

        now = time.time()
        size_bytes = (
            body.size_kb * 1024
            if body.size_kb is not None
            else sys.getsizeof(body.value)
        )
        entry = {
            "key": key,
            "value": body.value,
            "size_bytes": size_bytes,
            "created_at": now,
            "access_count": 0,
            "last_accessed": now,
        }
        cache[key] = entry

        memory_service_cache_entries.set(len(cache))
        memory_service_cache_size_kb.set(_estimated_total_kb())

        return {
            "key": entry["key"],
            "value": entry["value"],
            "size_bytes": entry["size_bytes"],
            "created_at": entry["created_at"],
            "access_count": entry["access_count"],
            "last_accessed": entry["last_accessed"],
        }


@app.delete("/cache/{key}")
async def delete_cache_entry(key: str):
    """Remove a cached entry by key.  Returns 404 if not found."""
    async with cache_lock:
        if key not in cache:
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found in cache")
        del cache[key]

    memory_service_cache_entries.set(len(cache))
    memory_service_cache_size_kb.set(_estimated_total_kb())

    return {"service": SERVICE_NAME, "deleted": key}


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Return detailed cache statistics including hit/miss ratio."""
    async with cache_lock:
        total = len(cache)
        if total == 0:
            return {
                "service": SERVICE_NAME,
                "total_entries": 0,
                "estimated_total_kb": 0,
                "max_entries": MAX_CACHE_ENTRIES,
                "hit_count": hit_count,
                "miss_count": miss_count,
                "hit_ratio": 0.0,
                "oldest_entry_age_seconds": None,
                "avg_access_count": 0.0,
            }

        now = time.time()
        oldest_created = min(entry["created_at"] for entry in cache.values())
        oldest_age = now - oldest_created
        total_access = sum(entry["access_count"] for entry in cache.values())
        avg_access = total_access / total
        estimated_kb = _estimated_total_kb()

    total_requests = hit_count + miss_count
    ratio = hit_count / total_requests if total_requests > 0 else 0.0

    return {
        "service": SERVICE_NAME,
        "total_entries": total,
        "estimated_total_kb": estimated_kb,
        "max_entries": MAX_CACHE_ENTRIES,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "hit_ratio": round(ratio, 4),
        "oldest_entry_age_seconds": round(oldest_age, 2),
        "avg_access_count": round(avg_access, 2),
    }

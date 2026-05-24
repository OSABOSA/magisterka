"""
CPU-bound microservice for scalability research.
FastAPI application with CPU-intensive image processing and Fibonacci computation.

Endpoints:
- GET  /health      – Health check
- GET  /metrics     – Prometheus metrics (auto-instrumented)
- POST /process     – CPU-intensive image processing
- GET  /fibonacci   – Recursive Fibonacci (CPU-bound, no I/O)
"""

from __future__ import annotations

import io
import math
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageFilter
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    service: str


class ProcessStats(BaseModel):
    mean_rgb: list[int]
    dominant_rgb: list[int]
    entropy: float


class ProcessResponse(BaseModel):
    service: str
    original_size: list[int]
    processed_size: list[int]
    filter_applied: str
    processing_time_ms: float
    stats: ProcessStats


class FibonacciResponse(BaseModel):
    service: str
    n: int
    fibonacci: int
    computation_time_ms: float


# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------

cpu_service_images_processed_total = Counter(
    "cpu_service_images_processed_total",
    "Total number of images processed by the CPU-service",
)

cpu_service_processing_duration_seconds = Histogram(
    "cpu_service_processing_duration_seconds",
    "Histogram of image processing duration in seconds",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# ---------------------------------------------------------------------------
# Helper: CPU-intensive Fibonacci (recursive, deliberately unoptimised)
# ---------------------------------------------------------------------------


def _fib_recursive(n: int) -> int:
    """Compute the n-th Fibonacci number using naive double recursion.

    This is intentionally O(2^n) — the worst possible implementation —
    to serve as a pure CPU-burn endpoint for scalability experiments.
    """
    if n <= 1:
        return n
    return _fib_recursive(n - 1) + _fib_recursive(n - 2)


# ---------------------------------------------------------------------------
# Helper: entropy calculation from histogram
# ---------------------------------------------------------------------------


def _shannon_entropy(histogram: list[int], total_pixels: int) -> float:
    """Calculate Shannon entropy from a pixel-value histogram."""
    entropy = 0.0
    for count in histogram:
        if count == 0:
            continue
        prob = count / total_pixels
        entropy -= prob * math.log2(prob)
    return entropy


# ---------------------------------------------------------------------------
# Helper: image processing pipeline
# ---------------------------------------------------------------------------


_AVAILABLE_FILTERS: dict[str, ImageFilter.Filter] = {
    "blur": ImageFilter.BLUR,
    "sharpen": ImageFilter.SHARPEN,
    "edge_enhance": ImageFilter.EDGE_ENHANCE,
}


def _process_image(
    image_data: bytes,
    width: int,
    height: int,
    filter_name: str,
) -> tuple[dict, list[int], list[int], str]:
    """Run the full CPU-intensive image processing pipeline.

    Returns (stats_dict, original_size, processed_size, filter_name).
    """
    img = Image.open(io.BytesIO(image_data))
    original_size = list(img.size)  # [W, H]

    # 1. Convert to RGB
    rgb_img = img.convert("RGB")

    # 2. Resize
    rgb_img = rgb_img.resize((width, height), Image.Resampling.LANCZOS)
    processed_size = [width, height]

    # 3. Apply filter
    pil_filter = _AVAILABLE_FILTERS.get(filter_name, ImageFilter.BLUR)
    filtered_img = rgb_img.filter(pil_filter)

    # 4. Histogram per channel (R, G, B)
    # Pillow histogram() returns a flat 768-element list: 256 for R, 256 for G, 256 for B
    flat_hist = filtered_img.histogram()
    r_hist = flat_hist[0:256]
    g_hist = flat_hist[256:512]
    b_hist = flat_hist[512:768]

    total_pixels = width * height

    # 5. Mean colour per channel
    mean_r = sum(i * count for i, count in enumerate(r_hist)) // max(total_pixels, 1)
    mean_g = sum(i * count for i, count in enumerate(g_hist)) // max(total_pixels, 1)
    mean_b = sum(i * count for i, count in enumerate(b_hist)) // max(total_pixels, 1)

    # 6. Dominant colour per channel (index of max bin)
    dom_r = r_hist.index(max(r_hist))
    dom_g = g_hist.index(max(g_hist))
    dom_b = b_hist.index(max(b_hist))

    # 7. Entropy (average across channels)
    entropy_r = _shannon_entropy(r_hist, total_pixels)
    entropy_g = _shannon_entropy(g_hist, total_pixels)
    entropy_b = _shannon_entropy(b_hist, total_pixels)
    avg_entropy = (entropy_r + entropy_g + entropy_b) / 3.0

    stats = {
        "mean_rgb": [mean_r, mean_g, mean_b],
        "dominant_rgb": [dom_r, dom_g, dom_b],
        "entropy": round(avg_entropy, 2),
    }

    return stats, original_size, processed_size, filter_name


# ---------------------------------------------------------------------------
# Lifespan — manage instrumentator lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: expose Prometheus metrics.  Shutdown: no-op."""
    # Instrumentator is already set up below (module-level).
    # The lifespan context is here for any future resource init/teardown.
    yield


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CPU-Service",
    description="CPU-bound microservice for scalability research (image processing + Fibonacci)",
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
    inprogress_name="cpu_service_http_requests_inprogress",
    inprogress_labels=True,
)

instrumentator.add(
    cpu_service_images_processed_total,
)
instrumentator.add(
    cpu_service_processing_duration_seconds,
)

instrumentator.instrument(app, metric_namespace="cpu_service")
# Expose /metrics endpoint
instrumentator.expose(app, endpoint="/metrics", include_in_schema=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for liveness/readiness probes."""
    return {"status": "ok", "service": "cpu-service"}


@app.post("/process", response_model=ProcessResponse)
async def process_image(
    file: UploadFile = File(...),
    width: int = Query(800, ge=1, le=4096, description="Target width in pixels"),
    height: int = Query(600, ge=1, le=4096, description="Target height in pixels"),
    filter: str = Query("blur", description="Filter: blur, sharpen, or edge_enhance"),
):
    """Accept an uploaded image and run a CPU-intensive processing pipeline.

    Steps:
    1. Convert to RGB
    2. Resize to (width, height)
    3. Apply chosen PIL filter
    4. Compute per-channel histogram, mean, dominant colour, and entropy
    5. Return processing stats and timing
    """
    if file.filename is None or file.filename == "":
        raise HTTPException(status_code=400, detail="No file provided")

    # Validate filter choice early
    if filter not in _AVAILABLE_FILTERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown filter '{filter}'. Supported: {list(_AVAILABLE_FILTERS.keys())}",
        )

    try:
        image_data = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    if not image_data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Time the processing
    start = time.perf_counter()
    try:
        stats, orig_size, proc_size, applied_filter = _process_image(
            image_data, width, height, filter
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid or unprocessable image: {str(exc)}",
        )
    elapsed = time.perf_counter() - start
    processing_time_ms = round(elapsed * 1000, 2)

    # Increment custom counter
    cpu_service_images_processed_total.inc()

    # Observe processing duration
    cpu_service_processing_duration_seconds.observe(elapsed)

    return {
        "service": "cpu-service",
        "original_size": orig_size,
        "processed_size": proc_size,
        "filter_applied": applied_filter,
        "processing_time_ms": processing_time_ms,
        "stats": stats,
    }


@app.get("/fibonacci", response_model=FibonacciResponse)
async def compute_fibonacci(
    n: int = Query(30, ge=0, le=40, description="Fibonacci index (0-40)"),
):
    """Compute the n-th Fibonacci number using naive double recursion.

    This endpoint is intentionally CPU-bound; the recursive implementation
    has O(2^n) complexity and performs zero I/O beyond the HTTP request.
    """
    start = time.perf_counter()
    result = _fib_recursive(n)
    elapsed = time.perf_counter() - start
    computation_time_ms = round(elapsed * 1000, 2)

    return {
        "service": "cpu-service",
        "n": n,
        "fibonacci": result,
        "computation_time_ms": computation_time_ms,
    }

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app.api.v1 import api_router
from app.config import settings
from app.core.auth import require_api_key, validate_api_key_config
from app.core.db import dispose_engine
from app.core.logging import configure_logging, get_logger
from app.core.readiness import router as readiness_router
from app.core.tracing import init_langsmith
from app.services.layer2.data_catalog import init_data

logger = get_logger(__name__)

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    init_langsmith()

    # Refuse to start on a bad API-key configuration (see app/core/auth.py).
    validate_api_key_config()

    # Load the Layer 2 catalogue and data assets once for this process (S1).
    # A missing or invalid required asset raises DataLoadError here, so the
    # process refuses to start instead of answering "unknown" for everything (S2).
    init_data()

    # Optionally widen the per-worker thread pool that backs `asyncio.to_thread`
    # (the blocking Layer 1 path). This lets a single worker run more concurrent
    # blocking LLM calls; combine with CORTEX_API_WORKERS for horizontal scale.
    executor: ThreadPoolExecutor | None = None
    if settings.cortex_api_thread_workers > 0:
        executor = ThreadPoolExecutor(
            max_workers=settings.cortex_api_thread_workers,
            thread_name_prefix="cortex-worker",
        )
        asyncio.get_running_loop().set_default_executor(executor)
        logger.info(
            "runtime.thread_pool configured max_workers=%d",
            settings.cortex_api_thread_workers,
        )

    yield

    if executor is not None:
        executor.shutdown(wait=False)
    await dispose_engine()


def _split_routes_by_auth(router: APIRouter) -> tuple[APIRouter, APIRouter]:
    """Split the API router into open routes and API-key protected routes.

    Everything under ``/api/v1`` requires an API key; the rest (``/health``)
    stays open. Splitting by path means a route added to ``api_router`` later is
    protected without anyone having to remember it.
    """
    open_router = APIRouter()
    protected_router = APIRouter()
    for route in router.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            raise RuntimeError(
                f"Cannot decide API-key protection for route {route!r}: it has no path."
            )
        target = protected_router if path.startswith(API_V1_PREFIX) else open_router
        target.routes.append(route)
    return open_router, protected_router


app = FastAPI(title="cortex-api", lifespan=lifespan)

_open_router, _protected_router = _split_routes_by_auth(api_router)
app.include_router(_open_router)
app.include_router(readiness_router)
app.include_router(_protected_router, dependencies=[Depends(require_api_key)])

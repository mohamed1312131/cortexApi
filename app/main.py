import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_router
from app.config import settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging, get_logger
from app.core.tracing import init_langsmith

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    init_langsmith()

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


app = FastAPI(title="cortex-api", lifespan=lifespan)
app.include_router(api_router)


from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.db import dispose_engine
from app.core.redis_client import close_redis_client
from app.core.tracing import init_langsmith


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_langsmith()
    yield
    await close_redis_client()
    await dispose_engine()


app = FastAPI(title="cortex-api", lifespan=lifespan)
app.include_router(api_router)


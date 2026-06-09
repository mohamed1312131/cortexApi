from redis.asyncio import Redis

from app.config import settings

redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> Redis:
    return redis_client


async def close_redis_client() -> None:
    await redis_client.aclose()


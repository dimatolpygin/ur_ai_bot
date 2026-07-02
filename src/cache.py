"""Redis-клиент для кеша и памяти диалога (отдельно от FSM-хранилища aiogram)."""
from __future__ import annotations

from redis.asyncio import Redis

from .config import settings
from .logger import logger

_redis: Redis | None = None


async def init_redis() -> Redis:
    global _redis
    if _redis is not None:
        return _redis
    _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await _redis.ping()
    logger.info("✅ Подключение к Redis установлено")
    return _redis


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis не инициализирован. Сначала вызовите init_redis().")
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Соединение с Redis закрыто")

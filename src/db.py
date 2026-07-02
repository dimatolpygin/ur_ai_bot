"""Пул соединений с Postgres. Работаем в выделенной схеме, чужие таблицы не трогаем."""
from __future__ import annotations

import asyncpg

from .config import settings
from .logger import logger

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Создаёт пул. search_path указывает на нашу схему, поэтому все запросы
    обращаются к таблицам внутри неё, не задевая существующие схемы.

    max_size с запасом под ориентир 500 одновременных пользователей — запросы к БД
    короткие, ИИ/поиск не держат соединение."""
    global _pool
    if _pool is not None:
        return _pool

    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=20,
        server_settings={"search_path": f"{settings.db_schema},public"},
    )
    logger.info(f"✅ Подключение к Postgres установлено (схема: {settings.db_schema})")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Пул БД не инициализирован. Сначала вызовите init_pool().")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Пул Postgres закрыт")

"""Память диалога в Redis: последние N сообщений пользователя и ассистента.

Отдельно от FSM-хранилища aiogram (там служебное состояние). Здесь — контекст для
модели: список {role, content}. Держим только хвост (кап `dialog_memory_messages`),
ставим TTL, чтобы брошенные диалоги не копились.
"""
from __future__ import annotations

import json

from redis.asyncio import Redis

from .config import settings

_PREFIX = "urist:dialog:"


def _key(tg_id: int) -> str:
    return f"{_PREFIX}{tg_id}"


async def get_history(redis: Redis, tg_id: int) -> list[dict[str, str]]:
    """Возвращает сохранённую историю диалога (может быть пустой)."""
    raw = await redis.get(_key(tg_id))
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def append(
    redis: Redis, tg_id: int, question: str, answer: str
) -> None:
    """Дописывает пару «вопрос-ответ» в историю, обрезая до капа, обновляет TTL."""
    history = await get_history(redis, tg_id)
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})
    # Оставляем только последние N сообщений (защита контекста и токенов).
    history = history[-settings.dialog_memory_messages:]
    await redis.set(
        _key(tg_id),
        json.dumps(history, ensure_ascii=False),
        ex=settings.dialog_ttl_seconds,
    )


async def clear(redis: Redis, tg_id: int) -> None:
    """Сбрасывает контекст диалога (кнопка «Новый диалог»)."""
    await redis.delete(_key(tg_id))

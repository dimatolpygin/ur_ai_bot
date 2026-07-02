"""Доступ к данным (asyncpg). На этапе 0 — пользователи, FSM-состояние, журнал событий.

Баланс запросов и прочие поля наращиваются на следующих этапах (миграциями).
"""
from __future__ import annotations

import asyncpg


async def upsert_user(
    pool: asyncpg.Pool, tg_id: int, username: str | None, first_name: str | None
) -> None:
    """Создаёт пользователя или обновляет его профиль и отметку активности.

    Баланс на INSERT берётся из настройки free_requests_on_start (server_default в
    миграции подставит его на этапе 1). Повторный /start НЕ обнуляет баланс.
    """
    await pool.execute(
        """
        INSERT INTO users (tg_id, username, first_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (tg_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_active = now()
        """,
        tg_id, username, first_name,
    )


async def set_fsm_state(pool: asyncpg.Pool, tg_id: int, state: str | None) -> None:
    """Пишет текущее «место» пользователя в боте — чтобы видеть, где он застрял."""
    await pool.execute(
        """
        INSERT INTO fsm_states (tg_id, state, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (tg_id) DO UPDATE
        SET state = EXCLUDED.state, updated_at = now()
        """,
        tg_id, state,
    )


async def add_event(pool: asyncpg.Pool, tg_id: int, action: str) -> None:
    """Журнал действий пользователя (для будущей аналитики пути, этап 9)."""
    await pool.execute(
        "INSERT INTO user_events (tg_id, action) VALUES ($1, $2)",
        tg_id, action,
    )

"""Доступ к данным (asyncpg). На этапе 0 — пользователи, FSM-состояние, журнал событий.

Баланс запросов и прочие поля наращиваются на следующих этапах (миграциями).
"""
from __future__ import annotations

import asyncpg


async def upsert_user(
    pool: asyncpg.Pool,
    tg_id: int,
    username: str | None,
    first_name: str | None,
    free_requests: int,
) -> tuple[bool, int]:
    """Создаёт пользователя или обновляет его профиль и отметку активности.

    Новичку на INSERT начисляется free_requests бесплатных запросов (значение
    конфигурируемо, поэтому подставляем его явно, а не server_default'ом).
    Повторный /start только освежает профиль и last_active — баланс НЕ трогает.

    Возвращает (is_new, balance): is_new=True, если строка только что создана
    (определяем по системному xmax=0 — на вставке он нулевой, на UPDATE — нет).
    """
    row = await pool.fetchrow(
        """
        INSERT INTO users (tg_id, username, first_name, balance)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tg_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_active = now()
        RETURNING (xmax = 0) AS is_new, balance
        """,
        tg_id, username, first_name, free_requests,
    )
    return bool(row["is_new"]), int(row["balance"])


async def get_balance(pool: asyncpg.Pool, tg_id: int) -> int:
    """Текущий баланс запросов пользователя (0, если записи почему-то нет)."""
    val = await pool.fetchval("SELECT balance FROM users WHERE tg_id = $1", tg_id)
    return int(val) if val is not None else 0


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

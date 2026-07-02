"""Доступ к данным (asyncpg). На этапе 0 — пользователи, FSM-состояние, журнал событий.

Баланс запросов и прочие поля наращиваются на следующих этапах (миграциями).
"""
from __future__ import annotations

from decimal import Decimal

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


async def charge_one(pool: asyncpg.Pool, tg_id: int) -> int | None:
    """Списывает 1 запрос атомарно. Возвращает новый баланс либо None, если

    списывать нечего (balance <= 0). Условие balance > 0 в UPDATE защищает от
    гонки и от ухода в минус: списание — единственная точка расхода (этап 2).
    """
    return await pool.fetchval(
        """
        UPDATE users SET balance = balance - 1
        WHERE tg_id = $1 AND balance > 0
        RETURNING balance
        """,
        tg_id,
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


# ── Платежи ЮKassa (этап 6) ──────────────────────────────────────────────────

async def create_payment(
    pool: asyncpg.Pool,
    *,
    yookassa_payment_id: str,
    idempotence_key: str,
    tg_id: int,
    package: int,
    amount: Decimal | int | float,
    confirmation_url: str | None,
    status: str = "pending",
) -> int:
    """Сохраняет строку создаваемого платежа (pending). Возвращает её id."""
    row = await pool.fetchrow(
        """
        INSERT INTO payments (
            yookassa_payment_id, idempotence_key, tg_id, package, amount,
            confirmation_url, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        yookassa_payment_id,
        idempotence_key,
        tg_id,
        package,
        Decimal(str(amount)),
        confirmation_url,
        status,
    )
    return int(row["id"])


async def get_payment_by_yk_id(
    pool: asyncpg.Pool, yk_id: str
) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT * FROM payments WHERE yookassa_payment_id = $1", yk_id
    )


async def get_pending_payments(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Незавершённые платежи — их опрашивает фоновый поллер."""
    return await pool.fetch(
        "SELECT * FROM payments WHERE status = 'pending' ORDER BY id"
    )


async def get_user_payments(
    pool: asyncpg.Pool, tg_id: int, limit: int = 10
) -> list[asyncpg.Record]:
    """История покупок пользователя (для экрана «Баланс и оплата»)."""
    return await pool.fetch(
        """
        SELECT package, amount, status, created_at
        FROM payments
        WHERE tg_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        tg_id, limit,
    )


async def mark_payment_canceled(pool: asyncpg.Pool, yk_id: str) -> None:
    await pool.execute(
        "UPDATE payments SET status = 'canceled', updated_at = now() "
        "WHERE yookassa_payment_id = $1 AND status = 'pending'",
        yk_id,
    )


async def credit_payment(
    pool: asyncpg.Pool, yk_id: str
) -> tuple[int | None, bool]:
    """Атомарно зачисляет пакет на баланс по успешному платежу. Идемпотентно.

    Берёт строку платежа под блокировку (FOR UPDATE). Если уже зачислено
    (credited=true) — ничего не делает: повторный вызов (поллер + кнопка
    «Проверить» одновременно, повторный succeeded) НЕ пополняет второй раз.
    Иначе: balance += package, is_paying=true, credited=true, status=succeeded.

    Возвращает (новый_баланс, credited_now):
      credited_now=True  — баланс пополнен этим вызовом (уведомить пользователя);
      credited_now=False — платёж не найден (баланс=None) либо уже был зачислен.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            pay = await conn.fetchrow(
                "SELECT * FROM payments WHERE yookassa_payment_id = $1 FOR UPDATE",
                yk_id,
            )
            if pay is None:
                return None, False
            if pay["credited"]:
                bal = await conn.fetchval(
                    "SELECT balance FROM users WHERE tg_id = $1", pay["tg_id"]
                )
                return (int(bal) if bal is not None else 0), False

            new_balance = await conn.fetchval(
                """
                UPDATE users SET balance = balance + $2, is_paying = true
                WHERE tg_id = $1
                RETURNING balance
                """,
                pay["tg_id"], pay["package"],
            )
            await conn.execute(
                "UPDATE payments SET status = 'succeeded', credited = true, "
                "updated_at = now() WHERE id = $1",
                pay["id"],
            )
            return (int(new_balance) if new_balance is not None else 0), True

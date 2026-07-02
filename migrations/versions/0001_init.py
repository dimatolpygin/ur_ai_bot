"""init: users + fsm_states + user_events

Revision ID: 0001_init
Revises:
Create Date: 2026-07-02

Базовые таблицы каркаса (этап 0). search_path указывает на схему urist_bot
(см. migrations/env.py), поэтому таблицы создаются именно в ней.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Пользователи бота. balance — баланс запросов к ИИ (валюта проекта: 1 запрос =
    # 1 ответ). is_paying — платил ли хоть раз (для шапки /admin, этап 7).
    op.create_table(
        "users",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_paying", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_active",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Текущее FSM-состояние пользователя — чтобы видеть, где он застрял.
    op.create_table(
        "fsm_states",
        sa.Column("tg_id", sa.BigInteger(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_fsm_states_state", "fsm_states", ["state"])

    # Журнал действий пользователя — задел под аналитику пути (этап 9).
    op.create_table(
        "user_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_user_events_tg_id", "user_events", ["tg_id"])
    op.create_index("ix_user_events_created_at", "user_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_user_events_created_at", table_name="user_events")
    op.drop_index("ix_user_events_tg_id", table_name="user_events")
    op.drop_table("user_events")
    op.drop_index("ix_fsm_states_state", table_name="fsm_states")
    op.drop_table("fsm_states")
    op.drop_table("users")

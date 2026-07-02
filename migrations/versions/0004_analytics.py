"""events — типизированный аналитический поток (этап 9)

Revision ID: 0004_analytics
Revises: 0003_admin
Create Date: 2026-07-03

Отдельная таблица под будущую аналитику/конверсию — НЕ смешиваем с `user_events`
(сырой firehose из middleware: каждое входящее сообщение/кнопка). Здесь только
осмысленные бизнес-события со стабильным кодом `type` и произвольной `meta` (JSONB):
регистрация, вопрос (выдан ответ), проверка работодателя, веб-поиск (на каждый
реальный вызов провайдера), создание и успех платежа, уточнение / off-topic.

UI/дашбордов на этом этапе НЕ делаем — только копим данные (см. CLAUDE.md). Индексы
на `type` и `(type, created_at)` — под будущие срезы конверсии и активности.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004_analytics"
down_revision: Union[str, None] = "0003_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        # Стабильный код события (register / question / web_search / payment_* …).
        sa.Column("type", sa.Text(), nullable=False),
        # Детали события: sources, provider, package, amount и т.п. Необязательно.
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_events_tg_id", "events", ["tg_id"])
    op.create_index("ix_events_type", "events", ["type"])
    # Под срезы «событий типа X во времени» (конверсия/активность по периодам).
    op.create_index("ix_events_type_created", "events", ["type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_events_type_created", table_name="events")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_tg_id", table_name="events")
    op.drop_table("events")

"""search_usage — счётчик расхода поисковиков (этап 7, админка)

Revision ID: 0003_admin
Revises: 0002_payments
Create Date: 2026-07-03

Шапка `/admin` показывает балансы сервисов. У OpenRouter есть реальный API остатка
кредитов (`GET /credits`), а у поисковиков (Tavily/Exa/Firecrawl) простого usage-API
нет — поэтому ведём СОБСТВЕННЫЙ счётчик израсходованных вызовов: инкремент на каждый
реальный вызов провайдера в `search.py`. Одна строка на провайдера.

Ключи поисковиков и цены переезжают в уже существующую `app_settings` (миграция
0002) — отдельной DDL не требуется: админка пишет туда ключи `search_key_<provider>`,
`price_pkg_*`, `price_per_request`; `settings_repo` читает их с фолбэком на `.env`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_admin"
down_revision: Union[str, None] = "0002_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    usage = op.create_table(
        "search_usage",
        # Имя провайдера (tavily / exa / firecrawl) — совпадает с ключами search.py.
        sa.Column("provider", sa.Text(), primary_key=True),
        # Сколько реальных вызовов к API провайдера сделано (расход квоты).
        sa.Column(
            "calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.bulk_insert(
        usage,
        [
            {"provider": "tavily", "calls": 0},
            {"provider": "exa", "calls": 0},
            {"provider": "firecrawl", "calls": 0},
        ],
    )


def downgrade() -> None:
    op.drop_table("search_usage")

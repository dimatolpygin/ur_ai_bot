"""payments + app_settings (этап 6)

Revision ID: 0002_payments
Revises: 0001_init
Create Date: 2026-07-03

Оплата пакетов запросов через ЮKassa (без вебхука — фоновый polling). Каждое
создание платежа порождает строку payments; при succeeded баланс пополняется
атомарно с флагом credited — это защита от двойного зачисления (поллер и кнопка
«Проверить оплату» могут сработать одновременно).

app_settings — key/value для горячих настроек (цены пакетов, цена запроса). На
этом этапе только сидим и читаем; редактирование из /admin — этап 7. Ключи
поисковиков сюда переедут тоже на этапе 7.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_payments"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Горячие настройки key/value. value — текст (число/строка), парсим на месте.
    app_settings = op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Стартовые значения. Цены пакетов (RUB) — плейсхолдеры под тест; владелец
    # выставит реальные (себестоимость + наценка) в /admin на этапе 7.
    op.bulk_insert(
        app_settings,
        [
            {"key": "price_pkg_10", "value": "199"},
            {"key": "price_pkg_20", "value": "349"},
            {"key": "price_pkg_30", "value": "499"},
            # Сколько единиц баланса списывается за один ответ (валюта — «запросы»).
            {"key": "price_per_request", "value": "1"},
        ],
    )

    # Платежи ЮKassa. package — размер пакета (запросов), начисляется при успехе.
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # id платежа в ЮKassa (UUID-строка). Уникален — по нему polling ищет платёж.
        sa.Column("yookassa_payment_id", sa.Text(), nullable=False, unique=True),
        # Ключ идемпотентности запроса создания — повтор не плодит платежи в ЮKassa.
        sa.Column("idempotence_key", sa.Text(), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        # Размер пакета в запросах (10/20/30) — столько зачислим на баланс.
        sa.Column("package", sa.Integer(), nullable=False),
        # Сумма к оплате в рублях на момент создания платежа.
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        # pending → succeeded / canceled (статус из ЮKassa).
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("confirmation_url", sa.Text(), nullable=True),
        # Флаг «баланс уже пополнен этим платежом» — идемпотентность зачисления.
        sa.Column(
            "credited", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_payments_status", "payments", ["status"])
    op.create_index("ix_payments_tg", "payments", ["tg_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_tg", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_table("payments")
    op.drop_table("app_settings")

"""users.email — email покупателя для чека 54-ФЗ

Revision ID: 0005_user_email
Revises: 0004_analytics
Create Date: 2026-07-06

Email спрашиваем у пользователя перед ПЕРВОЙ покупкой и сохраняем — дальше чек
ЮKassa уходит на него, повторно не спрашиваем. Nullable: у старых юзеров пусто,
заполнится при первой оплате (до этого в чек идёт заглушка из настроек).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_user_email"
down_revision: Union[str, None] = "0004_analytics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "email")

"""Alembic env (async). Работаем строго в схеме urist_bot — чужие таблицы не трогаем."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.config import settings

config = context.config

# URL и схема — из настроек проекта.
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)
SCHEMA = settings.db_schema

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Миграции пишем вручную, без autogenerate-моделей.
target_metadata = None


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        compare_type=True,
    )


def do_run_migrations(connection: Connection) -> None:
    # Никаких операций до begin_transaction: иначе SQLAlchemy 2.0 откроет транзакцию
    # раньше Alembic, тот решит, что ею управляют снаружи, и не закоммитит миграции.
    # Размещение таблиц в схеме urist_bot обеспечивает search_path из server_settings.
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Все соединения работают в нашей схеме.
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    # Схему создаём отдельным ЗАКОММИЧЕННЫМ шагом — она должна существовать до того,
    # как Alembic создаст в ней свою version-таблицу.
    async with connectable.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        await connection.commit()

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())

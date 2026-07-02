"""Автоприменение миграций Alembic при старте бота.

Вызывается синхронно ДО запуска основного event loop (см. main.py), потому что
Alembic в async-режиме сам поднимает временный event loop через asyncio.run().
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .logger import logger

_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _ROOT / "alembic.ini"


def run_migrations() -> None:
    """Применяет все новые миграции до head."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    logger.info("⏳ Применяю миграции Alembic (upgrade head)...")
    command.upgrade(cfg, "head")
    logger.info("✅ Миграции в актуальном состоянии")

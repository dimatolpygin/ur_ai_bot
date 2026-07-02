"""Слой логирования на Loguru. Читаемый вывод на русском для отладки.

Логируем каждое действие пользователя (см. middlewares.py): дата/время, username,
user_id, first_name, текст и ответ бота. Полнота логов — приоритет заказчика.
"""
from __future__ import annotations

import sys

from loguru import logger

_FORMAT = (
    "<green>{time:DD.MM.YYYY HH:mm:ss}</green> "
    "<level>{level: <7}</level> "
    "<level>{message}</level>"
)


def setup_logging(level: str = "INFO"):
    """Настраивает Loguru один раз при старте. Вывод — в stdout (видно в docker logs).

    Возвращает глобальный loguru-логгер для удобства (`log = setup_logging(...)`).
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level=level.upper(),
        format=_FORMAT,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )
    return logger


__all__ = ["logger", "setup_logging"]

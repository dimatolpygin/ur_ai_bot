"""Доступ к горячим настройкам (`app_settings`, key/value в БД).

Читаются на месте (объём мал, частота низкая) — значение всегда актуальное, без
рестарта (правка из /admin на этапе 7 применяется сразу). Сиды — в миграции 0002.
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg

# Ключи цен пакетов и цены запроса (сидятся в миграции 0002).
_PKG_KEY = {10: "price_pkg_10", 20: "price_pkg_20", 30: "price_pkg_30"}
PACKAGES: tuple[int, ...] = (10, 20, 30)


async def get_value(pool: asyncpg.Pool, key: str) -> str | None:
    return await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)


async def set_value(pool: asyncpg.Pool, key: str, value: str) -> None:
    """Пишет/обновляет настройку (используется админкой на этапе 7)."""
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key, value,
    )


async def package_price(pool: asyncpg.Pool, package: int) -> Decimal | None:
    """Цена пакета (RUB) из настроек. None — если пакет неизвестен/цена не задана."""
    key = _PKG_KEY.get(package)
    if key is None:
        return None
    raw = await get_value(pool, key)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (ValueError, ArithmeticError):
        return None


async def package_prices(pool: asyncpg.Pool) -> dict[int, Decimal]:
    """Карта {размер_пакета: цена} для показа на экране баланса."""
    out: dict[int, Decimal] = {}
    for pkg in PACKAGES:
        price = await package_price(pool, pkg)
        if price is not None:
            out[pkg] = price
    return out

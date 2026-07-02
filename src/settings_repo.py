"""Доступ к горячим настройкам (`app_settings`, key/value в БД).

Читаются на месте (объём мал, частота низкая) — значение всегда актуальное, без
рестарта (правка из /admin на этапе 7 применяется сразу). Сиды — в миграции 0002.
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg

from .config import settings

# Ключи цен пакетов и цены запроса (сидятся в миграции 0002).
_PKG_KEY = {10: "price_pkg_10", 20: "price_pkg_20", 30: "price_pkg_30"}
PACKAGES: tuple[int, ...] = (10, 20, 30)

# Поисковики: ключ в app_settings (правится из /admin) → фолбэк на .env. Порядок
# и имена совпадают с провайдерами в search.py.
_SEARCH_SETTING_KEY = {
    "tavily": "search_key_tavily",
    "exa": "search_key_exa",
    "firecrawl": "search_key_firecrawl",
}
_SEARCH_ENV_KEY = {
    "tavily": lambda: settings.tavily_api_key,
    "exa": lambda: settings.exa_api_key,
    "firecrawl": lambda: settings.firecrawl_api_key,
}
SEARCH_PROVIDERS: tuple[str, ...] = ("tavily", "exa", "firecrawl")


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


# ── Цена одного запроса (этап 7): сколько единиц баланса списывается за ответ ──

async def price_per_request(pool: asyncpg.Pool) -> int:
    """Стоимость одного ответа в единицах баланса. Дефолт 1 (как на этапах 2–6).

    Значение горячее: правится из /admin (`price_per_request` в app_settings),
    применяется без рестарта — `repo.charge_one` читает его на каждом списании.
    """
    raw = await get_value(pool, "price_per_request")
    if raw is None:
        return 1
    try:
        val = int(Decimal(raw))
    except (ValueError, ArithmeticError):
        return 1
    return max(val, 1)


# ── Ключи поисковиков (этап 7): app_settings → фолбэк на .env ─────────────────

async def search_key(pool: asyncpg.Pool, provider: str) -> str:
    """Ключ провайдера поиска. Сначала app_settings (правится из /admin, горячо),
    иначе — значение из .env (settings). Пусто → провайдер пропускается в поиске.
    """
    setting_key = _SEARCH_SETTING_KEY.get(provider)
    if setting_key is not None:
        raw = await get_value(pool, setting_key)
        if raw is not None and raw.strip():
            return raw.strip()
    env = _SEARCH_ENV_KEY.get(provider)
    return (env() if env else "").strip()


async def set_search_key(pool: asyncpg.Pool, provider: str, value: str) -> None:
    """Пишет ключ провайдера в app_settings (переопределяет .env, применяется горячо)."""
    setting_key = _SEARCH_SETTING_KEY[provider]
    await set_value(pool, setting_key, value.strip())


# ── Счётчик расхода поисковиков (этап 7): у провайдеров нет usage-API ─────────

async def bump_search_usage(pool: asyncpg.Pool, provider: str) -> None:
    """Инкремент счётчика реальных вызовов провайдера (для шапки /admin).

    Атомарно, не роняет поиск при ошибке БД (best-effort — вызывается из search.py).
    """
    try:
        await pool.execute(
            """
            INSERT INTO search_usage (provider, calls, updated_at)
            VALUES ($1, 1, now())
            ON CONFLICT (provider) DO UPDATE
            SET calls = search_usage.calls + 1, updated_at = now()
            """,
            provider,
        )
    except Exception:  # noqa: BLE001 — учёт расхода не должен ломать сам поиск
        pass


async def search_usage(pool: asyncpg.Pool) -> dict[str, int]:
    """Карта {провайдер: число_вызовов} для шапки админки."""
    rows = await pool.fetch("SELECT provider, calls FROM search_usage")
    out = {p: 0 for p in SEARCH_PROVIDERS}
    for r in rows:
        out[r["provider"]] = int(r["calls"])
    return out

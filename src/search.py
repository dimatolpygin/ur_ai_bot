"""Веб-поиск: единый интерфейс `run_web_search` поверх провайдеров.

Fallback-цепочка (§7 AGENT_PIPELINE): Tavily основной → Exa → Firecrawl запасные.
Пробуем по порядку, пропуская провайдеров без ключа; первый непустой результат
выигрывает. Всё упало / нет ключей → пустой список (модель честно скажет, что не
нашла). Каждый шаг и переключение логируем.

Ключи (этап 7): читаются горячо из `app_settings` (БД) через `settings_repo`, при
отсутствии — фолбэк на `.env`. Правка ключа из `/admin` применяется со следующего
поиска без рестарта. Каждый реальный вызов провайдера учитывается счётчиком расхода
(`settings_repo.bump_search_usage`) — у поисковиков нет простого usage-API, а шапка
`/admin` показывает израсходованное.
"""
from __future__ import annotations

import asyncpg
import aiohttp

from . import repo, settings_repo
from .config import settings
from .logger import logger

# Результат нормализуем к единой форме {title, url, snippet}.
Result = dict[str, str]

_SNIPPET_LIMIT = 500


def _clip(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= _SNIPPET_LIMIT else text[:_SNIPPET_LIMIT] + "…"


async def _tavily(session: aiohttp.ClientSession, query: str, key: str) -> list[Result]:
    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "max_results": settings.search_results_per_query,
    }
    async with session.post("https://api.tavily.com/search", json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    out: list[Result] = []
    for r in data.get("results", []):
        out.append(
            {
                "title": _clip(r.get("title")),
                "url": r.get("url", ""),
                "snippet": _clip(r.get("content")),
            }
        )
    return out


async def _exa(session: aiohttp.ClientSession, query: str, key: str) -> list[Result]:
    payload = {
        "query": query,
        "numResults": settings.search_results_per_query,
        "type": "auto",
        "contents": {"text": {"maxCharacters": _SNIPPET_LIMIT}},
    }
    headers = {"x-api-key": key}
    async with session.post(
        "https://api.exa.ai/search", json=payload, headers=headers
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    out: list[Result] = []
    for r in data.get("results", []):
        out.append(
            {
                "title": _clip(r.get("title")),
                "url": r.get("url", ""),
                "snippet": _clip(r.get("text")),
            }
        )
    return out


async def _firecrawl(session: aiohttp.ClientSession, query: str, key: str) -> list[Result]:
    payload = {"query": query, "limit": settings.search_results_per_query}
    headers = {"Authorization": f"Bearer {key}"}
    async with session.post(
        "https://api.firecrawl.dev/v1/search", json=payload, headers=headers
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
    out: list[Result] = []
    for r in data.get("data", []):
        out.append(
            {
                "title": _clip(r.get("title")),
                "url": r.get("url", ""),
                "snippet": _clip(r.get("description") or r.get("markdown")),
            }
        )
    return out


# Порядок провайдеров: (человекочитаемое имя, id для ключа/счётчика, функция).
# Ключ берётся горячо в run_web_search через settings_repo (БД → фолбэк .env).
_PROVIDERS = (
    ("Tavily", "tavily", _tavily),
    ("Exa", "exa", _exa),
    ("Firecrawl", "firecrawl", _firecrawl),
)


async def run_web_search(
    query: str, pool: asyncpg.Pool | None = None, tg_id: int | None = None
) -> list[Result]:
    """Ищет по цепочке провайдеров. Возвращает нормализованные результаты или [].

    `pool` (этап 7): источник горячих ключей из app_settings и счётчика расхода. Без
    пула — фолбэк на ключи из .env (совместимость), расход не учитывается.
    `tg_id` (этап 9): для аналитического события web_search на каждый вызов провайдера.
    """
    timeout = aiohttp.ClientTimeout(total=settings.search_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, pid, fn in _PROVIDERS:
            key = (
                await settings_repo.search_key(pool, pid)
                if pool is not None
                else _env_key(pid)
            )
            if not key:
                continue
            try:
                results = await fn(session, query, key)
            except Exception as e:  # noqa: BLE001 — переходим к следующему провайдеру
                logger.warning(f"Поиск {name} упал ({query!r}): {e!r} → следующий")
                continue
            # Вызов состоялся (ответ получен) — учитываем расход квоты провайдера
            # и пишем аналитическое событие поиска (этап 9).
            if pool is not None:
                await settings_repo.bump_search_usage(pool, pid)
                if tg_id is not None:
                    await repo.log_event(
                        pool, tg_id, repo.EVENT_WEB_SEARCH,
                        {"provider": pid, "results": len(results)},
                    )
            if results:
                logger.info(
                    f"Поиск {name}: {query!r} → {len(results)} рез. (провайдер выбран)"
                )
                return results
            logger.info(f"Поиск {name}: {query!r} → пусто, пробую следующий")

    logger.warning(f"Поиск: по {query!r} ничего не нашли (или нет ключей)")
    return []


def _env_key(pid: str) -> str:
    """Фолбэк-ключ из .env, когда run_web_search вызван без пула БД."""
    return {
        "tavily": settings.tavily_api_key,
        "exa": settings.exa_api_key,
        "firecrawl": settings.firecrawl_api_key,
    }.get(pid, "")

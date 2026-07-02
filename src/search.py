"""Веб-поиск: единый интерфейс `run_web_search` поверх провайдеров.

Fallback-цепочка (§7 AGENT_PIPELINE): Tavily основной → Exa → Firecrawl запасные.
Пробуем по порядку, пропуская провайдеров без ключа; первый непустой результат
выигрывает. Всё упало / нет ключей → пустой список (модель честно скажет, что не
нашла). Каждый шаг и переключение логируем.

Ключи пока из `.env` (settings). На этапе 7 источник станет `app_settings` (БД) с
горячей заменой из `/admin` — интерфейс функций менять не придётся.
"""
from __future__ import annotations

import aiohttp

from .config import settings
from .logger import logger

# Результат нормализуем к единой форме {title, url, snippet}.
Result = dict[str, str]

_SNIPPET_LIMIT = 500


def _clip(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= _SNIPPET_LIMIT else text[:_SNIPPET_LIMIT] + "…"


async def _tavily(session: aiohttp.ClientSession, query: str) -> list[Result]:
    payload = {
        "api_key": settings.tavily_api_key,
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


async def _exa(session: aiohttp.ClientSession, query: str) -> list[Result]:
    payload = {
        "query": query,
        "numResults": settings.search_results_per_query,
        "type": "auto",
        "contents": {"text": {"maxCharacters": _SNIPPET_LIMIT}},
    }
    headers = {"x-api-key": settings.exa_api_key}
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


async def _firecrawl(session: aiohttp.ClientSession, query: str) -> list[Result]:
    payload = {"query": query, "limit": settings.search_results_per_query}
    headers = {"Authorization": f"Bearer {settings.firecrawl_api_key}"}
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


# Порядок провайдеров и их ключи. Провайдер участвует, только если ключ задан.
_PROVIDERS = (
    ("Tavily", _tavily, lambda: settings.tavily_api_key),
    ("Exa", _exa, lambda: settings.exa_api_key),
    ("Firecrawl", _firecrawl, lambda: settings.firecrawl_api_key),
)


async def run_web_search(query: str) -> list[Result]:
    """Ищет по цепочке провайдеров. Возвращает нормализованные результаты или []."""
    timeout = aiohttp.ClientTimeout(total=settings.search_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, fn, key in _PROVIDERS:
            if not key():
                continue
            try:
                results = await fn(session, query)
            except Exception as e:  # noqa: BLE001 — переходим к следующему провайдеру
                logger.warning(f"Поиск {name} упал ({query!r}): {e!r} → следующий")
                continue
            if results:
                logger.info(
                    f"Поиск {name}: {query!r} → {len(results)} рез. (провайдер выбран)"
                )
                return results
            logger.info(f"Поиск {name}: {query!r} → пусто, пробую следующий")

    logger.warning(f"Поиск: по {query!r} ничего не нашли (или нет ключей)")
    return []

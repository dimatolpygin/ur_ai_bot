"""Клиент OpenRouter (OpenAI-совместимый chat/completions).

Этап 2: обычный ответ модели `model_answer` без веб-поиска. Инструменты и
tool-calling петля появятся на этапе 3. Используем aiohttp (уже в зависимостях
aiogram) — отдельный HTTP-клиент не тянем.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

import aiohttp

from . import search
from .config import settings
from .logger import logger

# Системный промпт: роль, аудитория, формат. Аудитория — «синие воротнички»
# 35–50 лет, поэтому язык простой. Формат строго HTML (Telegram), не Markdown.
SYSTEM_PROMPT = (
    "Ты — юридический помощник в Telegram-боте для рабочих людей (охранники, "
    "водители, монтажники и другие «синие воротнички»), которые ищут работу или "
    "уже работают. Отвечай по законодательству Российской Федерации.\n\n"
    "Как отвечать:\n"
    "- Простым, человеческим языком, без канцелярита и латыни. Аудитория — люди "
    "35–50 лет без юридического образования.\n"
    "- По существу и структурировано: если шагов несколько — короткий нумерованный "
    "или маркированный список.\n"
    "- Если вопрос требует уточнений, дай общий ответ и мягко скажи, что уточнить.\n"
    "- Не выдумывай номера статей и законов. Если не уверен в точной норме — скажи "
    "об этом и объясни суть без ложной конкретики.\n"
    "- В конце, когда уместно, короткая оговорка, что это справка, а не замена "
    "очной консультации юриста.\n"
    "- Не используй эмодзи.\n\n"
    "Форматирование — ТОЛЬКО HTML-теги Telegram: <b>жирный</b>, <i>курсив</i>, "
    "<u>подчёркнутый</u>, <code>моноширинный</code>. НЕ используй Markdown "
    "(никаких **, ##, ```). Не используй теги <ul>, <ol>, <li>, <p>, <br> — для "
    "списков ставь строки с «· » или «1. », перенос строки обычным \\n."
)


# Поисковый режим (этап 3): та же роль, но с инструментом web_search и ссылками.
SEARCH_SYSTEM_PROMPT = SYSTEM_PROMPT + (
    "\n\nУ тебя есть инструмент web_search для поиска актуальной информации в "
    "интернете. Используй его, когда нужны свежие данные, конкретные нормы, суммы, "
    "сроки, судебная практика или проверка фактов. Можешь искать несколько раз с "
    "разными запросами, если данных не хватает. Если инструмент ничего не вернул — "
    "честно скажи, что точных данных не нашёл, и дай общий ориентир. Опирайся на "
    "найденные источники, не выдумывай нормы и суммы."
)

# Схема инструмента (OpenAI-совместимый function calling).
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Ищет актуальную информацию в интернете (законы, сроки, суммы, практика, "
            "организации). Возвращает список результатов: заголовок, ссылка, фрагмент."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос на русском языке",
                }
            },
            "required": ["query"],
        },
    },
}

# Статусы для пользователя во время поиска («не молчим», §5.3).
_SEARCH_STATUSES = [
    "<i>Ищу информацию в интернете…</i>",
    "<i>Проверяю источники…</i>",
    "<i>Уточняю детали…</i>",
    "<i>Свожу данные воедино…</i>",
]
_FINALIZING = "<i>Формирую ответ…</i>"

NotifyFn = Callable[[str], Awaitable[None]]


class AIError(Exception):
    """Ошибка обращения к ИИ (сеть, HTTP, пустой ответ) — ловим в хендлере."""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # Необязательные, но рекомендованные OpenRouter заголовки атрибуции.
        "HTTP-Referer": "https://t.me/URIST2026_1_BOT",
        "X-Title": "URIST2026",
    }


async def _post_chat(session: aiohttp.ClientSession, payload: dict) -> dict:
    """Один вызов chat/completions. Возвращает распарсенный ответ или бросает AIError."""
    url = f"{settings.ai_base_url}/chat/completions"
    try:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"OpenRouter HTTP {resp.status}: {body[:500]}")
                raise AIError(f"HTTP {resp.status}")
            return await resp.json()
    except aiohttp.ClientError as e:
        logger.error(f"OpenRouter сетевая ошибка: {e!r}")
        raise AIError("сеть") from e


def _extract_text(data: dict) -> str:
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"OpenRouter неожиданный ответ: {str(data)[:500]}")
        raise AIError("формат ответа") from e


def _usage_tokens(data: dict) -> int:
    usage = data.get("usage") or {}
    return int(usage.get("total_tokens") or 0)


async def answer(history: list[dict[str, str]], question: str) -> str:
    """Простой ответ модели без инструментов (этап 2). Возвращает текст или AIError."""
    if not settings.openrouter_api_key:
        raise AIError("OPENROUTER_API_KEY не задан")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    payload = {
        "model": settings.model_answer,
        "messages": messages,
        "temperature": settings.ai_temperature,
        "max_tokens": settings.ai_max_tokens,
    }
    timeout = aiohttp.ClientTimeout(total=settings.ai_request_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _post_chat(session, payload)
    text = _extract_text(data)
    if not text:
        raise AIError("пустой ответ")
    logger.info(f"ИИ ответил ({settings.model_answer}): токены {_usage_tokens(data)}")
    return text


async def answer_with_search(
    history: list[dict[str, str]],
    question: str,
    notify: NotifyFn | None = None,
) -> tuple[str, list[str]]:
    """Агент с веб-поиском (этап 3): tool-calling петля с капами и форс-финалом.

    Модель сама решает, искать ли и сколько (кап `max_search_steps` + токен-бюджет).
    Возвращает (текст_ответа, список_источников). Списание — в хендлере, только при
    успешной выдаче. `notify(text)` — статусы пользователю во время поиска.
    """
    if not settings.openrouter_api_key:
        raise AIError("OPENROUTER_API_KEY не задан")

    messages: list[dict] = [{"role": "system", "content": SEARCH_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    sources: list[str] = []
    total_tokens = 0
    timeout = aiohttp.ClientTimeout(total=settings.ai_request_timeout)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for step in range(settings.max_search_steps):
            # Первый шаг форсируем: ответ обязан опираться на веб-поиск (грундинг,
            # §1.2), иначе модель ленится и отвечает по устаревшей памяти. Дальше —
            # авто: модель сама решает, добирать ли ещё или финализировать.
            tool_choice = (
                {"type": "function", "function": {"name": "web_search"}}
                if step == 0
                else "auto"
            )
            payload = {
                "model": settings.model_answer,
                "messages": messages,
                "temperature": settings.ai_temperature,
                "max_tokens": settings.ai_max_tokens,
                "tools": [WEB_SEARCH_TOOL],
                "tool_choice": tool_choice,
            }
            data = await _post_chat(session, payload)
            total_tokens += _usage_tokens(data)
            msg = data["choices"][0]["message"]
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                text = (msg.get("content") or "").strip()
                if not text:
                    raise AIError("пустой ответ")
                logger.info(
                    f"Агент: финал на шаге {step} без форса; токены≈{total_tokens}; "
                    f"источников {len(sources)}"
                )
                return text, _dedup(sources)

            # Модель просит поиск: фиксируем её ход и выполняем инструменты.
            messages.append(msg)
            if notify:
                await notify(_SEARCH_STATUSES[min(step, len(_SEARCH_STATUSES) - 1)])

            for call in tool_calls:
                query = _parse_query(call)
                results = await search.run_web_search(query)
                for r in results:
                    if r.get("url"):
                        sources.append(r["url"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": "web_search",
                        "content": json.dumps(results, ensure_ascii=False),
                    }
                )

            if total_tokens >= settings.search_token_budget:
                logger.info(f"Агент: токен-бюджет исчерпан ({total_tokens}) → форс-финал")
                break

        # Упор в кап шагов или бюджет → форс-финал без инструментов.
        if notify:
            await notify(_FINALIZING)
        payload = {
            "model": settings.model_answer,
            "messages": messages,
            "temperature": settings.ai_temperature,
            "max_tokens": settings.ai_max_tokens,
            "tool_choice": "none",
        }
        data = await _post_chat(session, payload)
        total_tokens += _usage_tokens(data)

    text = _extract_text(data)
    if not text:
        raise AIError("пустой ответ")
    logger.info(
        f"Агент: форс-финал; токены≈{total_tokens}; источников {len(sources)}"
    )
    return text, _dedup(sources)


def _parse_query(call: dict) -> str:
    """Достаёт аргумент query из tool_call (arguments — JSON-строка)."""
    try:
        args = json.loads(call["function"]["arguments"] or "{}")
        return str(args.get("query", "")).strip()
    except (KeyError, TypeError, json.JSONDecodeError):
        return ""


def _dedup(urls: list[str]) -> list[str]:
    """Уникальные источники с сохранением порядка появления."""
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

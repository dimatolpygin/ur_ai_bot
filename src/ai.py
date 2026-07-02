"""Клиент OpenRouter (OpenAI-совместимый chat/completions).

Этап 2: обычный ответ модели `model_answer` без веб-поиска. Инструменты и
tool-calling петля появятся на этапе 3. Используем aiohttp (уже в зависимостях
aiogram) — отдельный HTTP-клиент не тянем.
"""
from __future__ import annotations

import aiohttp

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


class AIError(Exception):
    """Ошибка обращения к ИИ (сеть, HTTP, пустой ответ) — ловим в хендлере."""


async def answer(history: list[dict[str, str]], question: str) -> str:
    """Запрашивает ответ модели. history — прошлые сообщения диалога (память).

    Возвращает непустой текст ответа или бросает AIError. Списание запроса —
    зона ответственности хендлера и происходит ТОЛЬКО при успешном ответе.
    """
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
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # Необязательные, но рекомендованные OpenRouter заголовки атрибуции.
        "HTTP-Referer": "https://t.me/URIST2026_1_BOT",
        "X-Title": "URIST2026",
    }
    url = f"{settings.ai_base_url}/chat/completions"
    timeout = aiohttp.ClientTimeout(total=settings.ai_request_timeout)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"OpenRouter HTTP {resp.status}: {body[:500]}")
                    raise AIError(f"HTTP {resp.status}")
                data = await resp.json()
    except aiohttp.ClientError as e:
        logger.error(f"OpenRouter сетевая ошибка: {e!r}")
        raise AIError("сеть") from e

    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"OpenRouter неожиданный ответ: {str(data)[:500]}")
        raise AIError("формат ответа") from e

    if not text:
        raise AIError("пустой ответ")

    usage = data.get("usage") or {}
    logger.info(
        f"ИИ ответил ({settings.model_answer}): токены "
        f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}"
    )
    return text

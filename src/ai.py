"""Клиент OpenRouter (OpenAI-совместимый chat/completions).

Этап 2: обычный ответ модели `model_answer` без веб-поиска. Инструменты и
tool-calling петля появятся на этапе 3. Используем aiohttp (уже в зависимостях
aiogram) — отдельный HTTP-клиент не тянем.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

import asyncpg
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
    "Сначала ВНИКНИ в конкретную ситуацию человека, а не выдавай общие советы:\n"
    "- Пойми, что именно у него происходит и что для него сейчас ГЛАВНОЕ (его "
    "настоящая тревога) — и отвечай в первую очередь на неё.\n"
    "- Учитывай его конкретные обстоятельства и ограничения (например: работал "
    "неофициально, нет документов, боится последствий). Совет должен подходить "
    "именно ему, а не «любому работнику».\n"
    "- Не вываливай универсальный чек-лист. Расставь приоритеты: что делать в "
    "первую очередь именно в его случае, что реально поможет, а что нет. Честно "
    "назови риски и реалистичные шансы.\n"
    "- Если в ситуации есть тяжёлый выбор или противоречие (например, получить "
    "деньги, но рискнуть чем-то важным) — назови его прямо и помоги взвесить.\n\n"
    "Как отвечать:\n"
    "- Простым, человеческим языком, без канцелярита и латыни. Аудитория — люди "
    "35–50 лет без юридического образования.\n"
    "- По делу, но не ради галочки: нумерованный список — только когда шагов "
    "правда несколько; иначе отвечай живо и связно, без формального шаблона.\n"
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
    "\n\nУ тебя есть инструмент web_search. Ищи ТОЧЕЧНО под конкретную ситуацию "
    "человека — его проблема ПЛЮС его обстоятельства (статус, регион, тип "
    "занятости), а не общими словами. Плохой запрос: «трудоустройство "
    "иностранцев». Хорошие: «как взыскать зарплату неофициальному работнику без "
    "договора», «риск выдворения при жалобе в трудовую инспекцию мигранту без "
    "документов». Если результаты не по теме — переформулируй и поищи ещё (у тебя "
    "несколько попыток). Бери из результатов только то, что реально относится к "
    "его случаю; нерелевантное в ответ не тащи. Не выдумывай нормы и суммы; если "
    "точных данных нет — честно скажи и дай практический ориентир по сути."
)

# ── Служебный слой (COLLECTING, этап 4): сбор ситуации на flash-lite ──────────
# Дешёвая модель добирает недостающие детали расплывчатого вопроса и возвращает
# СТРОГО JSON-контракт (см. AGENT_PIPELINE §4.2). Финальный ответ — не её работа.
COLLECT_SYSTEM_PROMPT = (
    "Ты — помощник по трудовым и бытовым юридическим вопросам для рабочих людей "
    "(охранники, водители, монтажники и другие «синие воротнички»). По сообщениям "
    "пользователя пойми его ситуацию и реши: данных уже достаточно для точного "
    "поиска ответа, или нужно задать ОДИН уточняющий вопрос.\n\n"
    "Верни СТРОГО один JSON-объект, без пояснений и без Markdown, по схеме:\n"
    "{\n"
    '  "off_topic": true|false,   // true, если сообщение НЕ про работу, право, '
    "трудовые отношения, деньги за работу и т.п.\n"
    '  "enough": true|false,      // true, если данных уже хватает для ответа\n'
    '  "confidence": 0.0..1.0,    // уверенность, что данных ХВАТАЕТ для точного '
    "ответа (не просто что понял тему). Задаёшь уточнение — ставь НИЗКИЙ (< 0.7)\n"
    '  "case": {\n'
    '     "problem_type": строка|null,  // не платят зарплату | увольнение | штрафы '
    "| проверка работодателя | трудовой договор | отпуск/больничный | другое\n"
    '     "region": строка|null,\n'
    '     "employment": строка|null,     // официально | неофициально | ГПХ | '
    "самозанятый | неизвестно\n"
    '     "timeline": строка|null,        // когда произошло, сроки\n'
    '     "documents": строка|null,       // какие документы есть\n'
    '     "goal": строка|null,            // чего хочет добиться\n'
    '     "details": строка|null          // краткая суть своими словами\n'
    "  },\n"
    '  "missing": [строки],          // каких важных слотов не хватает\n'
    '  "next_question": строка,      // ОДИН простой уточняющий вопрос ('
    'пусто, если enough=true)\n'
    '  "quick_replies": [строки]     // 2-4 коротких варианта ответа (или [])\n'
    "}\n\n"
    "Правила:\n"
    "- Уточняющих вопросов — МИНИМУМ. Спрашивай только по-настоящему важное: обычно "
    "хватает понять тип проблемы и суть. Регион и сроки важны для сроков давности и "
    "практики — уточняй их, только если это реально нужно для ответа.\n"
    "- Если пользователь описал ситуацию ясно — сразу enough=true, confidence высокий, "
    "next_question пустой. Если чего-то важного не хватает — enough=false, confidence "
    "низкий (< 0.7) и задай ОДИН вопрос.\n"
    "- Вопрос задавай простым, человеческим языком (аудитория 35–50 лет без юр-образования).\n"
    "- quick_replies — короткие (1-3 слова), которыми реально удобно ответить.\n"
    "- Тему бери СТРОГО из последнего сообщения пользователя. Если он спросил о "
    "новом (например, про криптовалюту, налоги, кредит) — разбирай именно это, а не "
    "предыдущую тему. Не притягивай прошлую тему к новому вопросу.\n"
    "- Не повторяй уже заданный вопрос и не спрашивай то, что уже есть в карточке case. "
    "Если ты уже задавал уточнение, а пользователь ответил уклончиво, коротко «нет»/"
    "«не знаю» или не по делу — НЕ повторяй тот же вопрос, ставь enough=true и иди к "
    "ответу по тому, что есть.\n"
    "- Если пользователь уходит от ответа — считай, что данных достаточно (enough=true)."
)

# Слоты карточки — фиксированный набор (AGENT_PIPELINE §4.1).
_CASE_SLOTS = (
    "problem_type",
    "region",
    "employment",
    "timeline",
    "documents",
    "goal",
    "details",
)


# Модель иногда роняет Markdown вопреки промпту, а Telegram-HTML его не рендерит —
# тогда в тексте видны голые «*» и «**». Детерминированно подчищаем перед отдачей.
_MD_BULLET = re.compile(r"^([ \t]*)[*\-•][ \t]+", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _tidy_reply(text: str) -> str:
    """Чистка Markdown-остатков: **жирный** → <b>, маркеры */-/• → «· »."""
    text = _MD_BOLD.sub(r"<b>\1</b>", text)
    text = _MD_BULLET.sub(lambda m: f"{m.group(1)}· ", text)
    return text


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


async def account_credits() -> tuple[float, float] | None:
    """Остаток кредитов OpenRouter для шапки /admin (этап 7): `GET /credits`.

    Возвращает (остаток, всего_потрачено) в долларах или None при недоступности.
    Ответ вида {"data": {"total_credits": X, "total_usage": Y}} → остаток = X − Y.
    Ошибки не бросаем: админка должна открыться даже без связи с OpenRouter.
    """
    if not settings.openrouter_api_key:
        return None
    url = f"{settings.ai_base_url}/credits"
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_headers()) as resp:
                if resp.status != 200:
                    logger.warning(f"OpenRouter /credits HTTP {resp.status}")
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, Exception) as e:  # noqa: BLE001 — шапка не критична
        logger.warning(f"OpenRouter /credits недоступен: {e!r}")
        return None
    d = data.get("data") or {}
    try:
        total = float(d.get("total_credits") or 0)
        usage = float(d.get("total_usage") or 0)
    except (TypeError, ValueError):
        return None
    return total - usage, usage


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
    pool: asyncpg.Pool | None = None,
    tg_id: int | None = None,
) -> tuple[str, list[str]]:
    """Агент с веб-поиском (этап 3): tool-calling петля с капами и форс-финалом.

    Модель сама решает, искать ли и сколько (кап `max_search_steps` + токен-бюджет).
    Возвращает (текст_ответа, список_источников). Списание — в хендлере, только при
    успешной выдаче. `notify(text)` — статусы пользователю во время поиска. `pool`
    (этап 7) прокидывается в поиск для горячих ключей и учёта расхода провайдеров;
    `tg_id` (этап 9) — для аналитического события web_search на каждый вызов поиска.
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
                return _tidy_reply(text), _dedup(sources)

            # Модель просит поиск: фиксируем её ход и выполняем инструменты.
            messages.append(msg)
            if notify:
                await notify(_SEARCH_STATUSES[min(step, len(_SEARCH_STATUSES) - 1)])

            for call in tool_calls:
                query = _parse_query(call)
                results = await search.run_web_search(query, pool, tg_id)
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
    return _tidy_reply(text), _dedup(sources)


def _compact_dialog(history: list[dict[str, str]], max_chars: int = 400) -> str:
    """Компактная выжимка памяти диалога для служебной модели: вопросы юзера
    целиком, длинные ответы бота подрезаем — нужен контекст темы, не весь текст."""
    lines: list[str] = []
    for m in history:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            lines.append(f"Пользователь: {content}")
        else:
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            lines.append(f"Бот: {content}")
    return "\n".join(lines)


async def collect_decide(
    collect_history: list[dict], case: dict, history: list[dict] | None = None
) -> dict:
    """Служебное решение сбора ситуации (этап 4) на flash-lite.

    Принимает диалог сбора (реплики юзера и наши уточнения) + уже собранную карточку
    `case` + память прошлого диалога `history` (для повторных вопросов той же темы —
    иначе сбор переспрашивает уже обсуждённое). Возвращает нормализованный dict по
    контракту §4.2: off_topic / enough / confidence / case / missing / next_question /
    quick_replies. Дешёвая модель, JSON.
    """
    if not settings.openrouter_api_key:
        raise AIError("OPENROUTER_API_KEY не задан")

    messages: list[dict] = [
        {"role": "system", "content": COLLECT_SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Уже собранная карточка case (учитывай, не спрашивай повторно):\n"
                + json.dumps(case, ensure_ascii=False)
            ),
        },
    ]
    # Память прошлого диалога: даёт контекст повторным/уточняющим вопросам той же
    # темы. Без неё сбор стартует «с чистого листа» и переспрашивает известное.
    if history:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Ниже — прошлые вопросы этого пользователя, ТОЛЬКО как справочный "
                    "фон. Тему и уточнения определяй по ТЕКУЩЕМУ (последнему) сообщению "
                    "пользователя, а не по этому фону. Прошлую тему учитывай, лишь "
                    "когда текущее сообщение её ЯВНО продолжает (уточняет её или "
                    "ссылается на неё) — тогда не переспрашивай уже сказанное и, если "
                    "картина ясна, ставь enough=true. Если же текущий вопрос о другом — "
                    "полностью ИГНОРИРУЙ прошлые темы: не переноси их слоты в case, не "
                    "задавай уточнений из старой темы, не предполагай, что речь снова "
                    "о них.\n" + _compact_dialog(history)
                ),
            }
        )
    messages.extend(collect_history)

    payload = {
        "model": settings.model_service,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": settings.collect_max_tokens,
        "response_format": {"type": "json_object"},
    }
    timeout = aiohttp.ClientTimeout(total=settings.ai_request_timeout)
    last_reason = "нет ответа"

    # Провайдер OpenRouter под flash-lite периодически (~20%) отдаёт
    # finish_reason=error с пустым/обрезанным JSON. Ретраим на дешёвой модели —
    # без ретраев сбор молча уходил бы в поиск, не задав уточнение.
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(1, settings.collect_retries + 1):
            data = await _post_chat(session, payload)
            choice = (data.get("choices") or [{}])[0]
            finish = choice.get("finish_reason")
            raw = (choice.get("message") or {}).get("content") or ""

            if finish == "error" or not raw.strip():
                last_reason = f"finish={finish}, пусто={not raw.strip()}"
                logger.warning(
                    f"Сбор ситуации: битый ответ ({last_reason}), попытка "
                    f"{attempt}/{settings.collect_retries}"
                )
                continue

            try:
                decision = _normalize_decision(raw)
            except AIError:
                last_reason = "не разобрать JSON"
                logger.warning(
                    f"Сбор ситуации: {last_reason}, попытка "
                    f"{attempt}/{settings.collect_retries}"
                )
                continue

            logger.info(
                f"Сбор ситуации ({settings.model_service}): токены "
                f"{_usage_tokens(data)}, попытка {attempt}"
            )
            return decision

    raise AIError(f"сбор: {last_reason} после {settings.collect_retries} попыток")


def _loads_lenient(raw: str) -> dict:
    """Парсит JSON от модели, терпимо к обёрткам (```json …``` / текст вокруг)."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Достаём первый {...}-блок, если модель обернула JSON в текст/фенсы.
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise AIError("сбор: не разобрать JSON")


def _normalize_decision(raw: str) -> dict:
    """Приводит ответ модели к безопасному контракту (гарантированные ключи/типы)."""
    parsed = _loads_lenient(raw)

    case_in = parsed.get("case")
    case_in = case_in if isinstance(case_in, dict) else {}
    case: dict[str, str | None] = {}
    for slot in _CASE_SLOTS:
        v = case_in.get(slot)
        case[slot] = v.strip() if isinstance(v, str) and v.strip() else None

    conf = parsed.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.0
    conf = min(max(conf, 0.0), 1.0)

    qr = parsed.get("quick_replies")
    qr = qr if isinstance(qr, list) else []
    quick_replies = [str(x).strip() for x in qr if str(x).strip()][:4]

    missing = parsed.get("missing")
    missing = [str(x) for x in missing] if isinstance(missing, list) else []

    return {
        "off_topic": bool(parsed.get("off_topic")),
        "enough": bool(parsed.get("enough")),
        "confidence": conf,
        "case": case,
        "missing": missing,
        "next_question": str(parsed.get("next_question") or "").strip(),
        "quick_replies": quick_replies,
    }


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

"""Ветка «Задать вопрос»: адаптивный сбор ситуации (этап 4) + ответ ИИ + списание.

Поток (AGENT_PIPELINE §3–4):
  «Задать вопрос» → при balance=0 уводим в оплату; иначе ждём вопрос.
  Первый текст → состояние COLLECTING: служебная модель (flash-lite) решает —
  данных хватает или задать уточнение. Уточнения БЕСПЛАТНЫ, идут по кругу под капом
  MAX_COLLECT_STEPS с ранним выходом по confidence и кнопкой «Ответить сейчас».
  Когда данных достаточно → SEARCHING+ANSWERING (веб-поиск), и ТОЛЬКО там —
  единственное списание 1 запроса. Off-topic / отмена / ошибка → без списания.

Кнопки навигации в состояниях перехватываются раньше, чтобы их текст не ушёл в
модель как вопрос/ответ.
"""
from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LinkPreviewOptions, Message
from redis.asyncio import Redis

from .. import ai, keyboards, memory, repo, texts
from ..config import settings
from ..logger import logger

router = Router()


class AskStates(StatesGroup):
    waiting_question = State()  # ждём первый вопрос
    collecting = State()  # идёт сбор ситуации (уточнения flash-lite)


# Ярлыки меню: в режимах ожидания/сбора их текст НЕ уходит в модель, а работает как
# навигация (проваливается в menu-роутер / обработчики ниже).
_MENU_LABELS = {
    texts.BTN_ASK,
    texts.BTN_EMPLOYER,
    texts.BTN_BALANCE,
    texts.BTN_HELP,
    texts.BTN_MAIN_MENU,
    texts.BTN_NEW_DIALOG,
}
# Управляющие кнопки этапа сбора — обрабатываются отдельными хендлерами, не моделью.
_COLLECT_CONTROLS = {texts.BTN_ANSWER_NOW, texts.BTN_CANCEL}
_STOP_LABELS = _MENU_LABELS | _COLLECT_CONTROLS

# Подписи слотов карточки для компактной сводки в поиск (details добавляем отдельно).
_SLOT_LABELS = {
    "problem_type": "Тип проблемы",
    "region": "Регион",
    "employment": "Форма занятости",
    "timeline": "Сроки/когда",
    "documents": "Документы",
    "goal": "Цель",
}


@router.message(F.text == texts.BTN_ASK)
async def enter_ask(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    """Вход в ветку из главного меню."""
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    if balance <= 0:
        await state.clear()
        await repo.set_fsm_state(pool, u.id, "screen:balance")
        await message.answer(
            texts.ask_need_payment(balance), reply_markup=keyboards.screen_nav()
        )
        logger.info(f"🤖 Бот → @{u.username or '—'}: вопрос при balance=0 → оплата")
        return

    await state.set_state(AskStates.waiting_question)
    await state.set_data({})
    await repo.set_fsm_state(pool, u.id, "screen:ask")
    await message.answer(texts.ask_prompt(balance), reply_markup=keyboards.ask_screen())
    logger.info(f"🤖 Бот → @{u.username or '—'}: вход в «Задать вопрос» (баланс {balance})")


@router.message(AskStates.waiting_question, F.text == texts.BTN_NEW_DIALOG)
async def new_dialog(message: Message, redis: Redis) -> None:
    """Сброс контекста диалога, остаёмся в режиме ожидания вопроса."""
    u = message.from_user
    await memory.clear(redis, u.id)
    await message.answer(texts.NEW_DIALOG_DONE, reply_markup=keyboards.ask_screen())
    logger.info(f"🤖 Бот → @{u.username or '—'}: сброшен диалог (Новый диалог)")


@router.message(AskStates.collecting, F.text == texts.BTN_ANSWER_NOW)
async def answer_now(
    message: Message, pool: asyncpg.Pool, redis: Redis, state: FSMContext
) -> None:
    """«Ответить сейчас»: обрываем сбор, идём к ответу по тому, что уже собрано."""
    u = message.from_user
    if await _guard_balance(message, pool, state):
        return
    data = await state.get_data()
    case = data.get("case") or {}
    original_question = data.get("original_question") or ""
    logger.info(f"🤖 Бот → @{u.username or '—'}: «Ответить сейчас» → финал")
    await _finalize_answer(message, pool, redis, state, case, original_question)


@router.message(AskStates.collecting, F.text == texts.BTN_CANCEL)
async def cancel_collect(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """«Отмена»: выходим в главное меню без списания."""
    u = message.from_user
    await state.clear()
    balance = await repo.get_balance(pool, u.id)
    await repo.set_fsm_state(pool, u.id, "screen:main_menu")
    await message.answer(
        texts.welcome_back(u.first_name, balance), reply_markup=keyboards.main_menu()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: отмена сбора → главное меню")


@router.message(
    AskStates.waiting_question,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(_STOP_LABELS),
)
async def start_collecting(
    message: Message, pool: asyncpg.Pool, redis: Redis, state: FSMContext
) -> None:
    """Первый текст в ветке → старт сбора ситуации (или сразу ответ, если полно)."""
    await _collect_step(message, pool, redis, state, fresh=True)


@router.message(
    AskStates.collecting,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(_STOP_LABELS),
)
async def continue_collecting(
    message: Message, pool: asyncpg.Pool, redis: Redis, state: FSMContext
) -> None:
    """Ответ пользователя на уточнение → следующий шаг сбора."""
    await _collect_step(message, pool, redis, state, fresh=False)


async def _collect_step(
    message: Message,
    pool: asyncpg.Pool,
    redis: Redis,
    state: FSMContext,
    fresh: bool,
) -> None:
    """Один шаг сбора: спросить служебную модель и решить — уточнять или отвечать."""
    u = message.from_user
    incoming = (message.text or "").strip()
    if not incoming:
        return

    if await _guard_balance(message, pool, state):
        return

    data = {} if fresh else await state.get_data()
    case: dict = data.get("case") or {}
    step = int(data.get("collect_step") or 0)
    ch: list[dict] = list(data.get("collect_history") or [])
    original_question = data.get("original_question") or incoming
    ch.append({"role": "user", "content": incoming})

    await message.bot.send_chat_action(message.chat.id, "typing")
    thinking = await message.answer(texts.COLLECT_THINKING)

    try:
        d = await ai.collect_decide(ch, case)
    except ai.AIError as e:
        # Служебная модель сбойнула — не мучаем юзера, идём к ответу по тому, что есть.
        logger.warning(f"Сбор сбойнул @{u.username or '—'}: {e} → сразу к ответу")
        await _safe_delete(thinking)
        await _finalize_answer(message, pool, redis, state, case, original_question)
        return

    await _safe_delete(thinking)

    if d["off_topic"]:
        await repo.add_event(pool, u.id, "Off-topic (без списания)")
        await state.set_state(AskStates.waiting_question)
        await state.set_data({})
        await message.answer(
            texts.OFFTOPIC_REDIRECT, reply_markup=keyboards.ask_screen()
        )
        logger.info(f"🤖 Бот → @{u.username or '—'}: off-topic → редирект (без списания)")
        return

    # Мерджим только заполненные слоты — не затираем ранее собранное пустыми.
    for slot, value in (d["case"] or {}).items():
        if value:
            case[slot] = value
    step += 1

    reached_cap = step >= settings.max_collect_steps
    confident = d["confidence"] >= settings.collect_confidence
    if d["enough"] or confident or reached_cap:
        why = "enough" if d["enough"] else ("confidence" if confident else "cap")
        logger.info(
            f"Сбор завершён @{u.username or '—'}: шаг {step}, "
            f"confidence {d['confidence']:.2f}, причина {why} → поиск"
        )
        await _finalize_answer(message, pool, redis, state, case, original_question)
        return

    # Данных мало → задаём уточнение и остаёмся в сборе.
    nq = d["next_question"] or "Уточните, пожалуйста, детали вашей ситуации."
    ch.append({"role": "assistant", "content": nq})
    await state.set_state(AskStates.collecting)
    await state.set_data(
        {
            "case": case,
            "collect_step": step,
            "collect_history": ch,
            "original_question": original_question,
        }
    )
    await repo.add_event(pool, u.id, "Уточняющий вопрос")
    await message.answer(
        texts.collect_question(nq),
        reply_markup=keyboards.collecting_kb(d["quick_replies"]),
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: уточнение (шаг {step}/{settings.max_collect_steps}), "
        f"вариантов {len(d['quick_replies'])}"
    )


async def _finalize_answer(
    message: Message,
    pool: asyncpg.Pool,
    redis: Redis,
    state: FSMContext,
    case: dict,
    original_question: str,
) -> None:
    """SEARCHING + ANSWERING: веб-поиск по сводке ситуации и единственное списание."""
    u = message.from_user
    await message.bot.send_chat_action(message.chat.id, "typing")
    status = await message.answer(texts.THINKING)

    async def notify(text: str) -> None:
        """Обновляет статус поиска в одном сообщении («не молчим», §5.3)."""
        try:
            await status.edit_text(text)
        except Exception:  # noqa: BLE001 — совпадающий текст/сеть не критичны
            pass

    history = await memory.get_history(redis, u.id)
    summary = _build_summary(case, original_question)
    try:
        reply, sources = await ai.answer_with_search(history, summary, notify)
    except ai.AIError as e:
        logger.warning(f"ИИ не ответил @{u.username or '—'}: {e} — баланс не списан")
        await _safe_delete(status)
        await state.set_state(AskStates.waiting_question)
        await state.set_data({})
        await message.answer(texts.AI_ERROR, reply_markup=keyboards.ask_screen())
        return

    # Успех → списываем ровно здесь (единственная точка расхода за весь путь вопроса).
    new_balance = await repo.charge_one(pool, u.id)
    if new_balance is None:
        new_balance = 0
        logger.warning(f"@{u.username or '—'}: списание не прошло (баланс 0 в гонке)")

    # В память кладём исходный вопрос пользователя (без служебной сводки и источников).
    await memory.append(redis, u.id, original_question, reply)
    await repo.add_event(pool, u.id, "Ответ ИИ выдан")

    await _safe_delete(status)
    await state.set_state(AskStates.waiting_question)
    await state.set_data({})
    await message.answer(
        reply + texts.sources_block(sources) + texts.answer_footer(new_balance),
        reply_markup=keyboards.ask_screen(),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: ответ выдан (источников {len(sources)}), "
        f"баланс {new_balance}"
    )


def _build_summary(case: dict, original_question: str) -> str:
    """Компактная сводка ситуации для поисковой модели (§4.4 build_summary)."""
    lines = [f"Вопрос пользователя: {original_question}"]
    for slot, label in _SLOT_LABELS.items():
        value = case.get(slot)
        if value:
            lines.append(f"{label}: {value}")
    details = case.get("details")
    if details and details.strip() and details.strip() != original_question.strip():
        lines.append(f"Детали: {details}")
    return "\n".join(lines)


async def _guard_balance(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> bool:
    """Проверка баланса перед дорогой операцией. True = увели в оплату, поток стоп."""
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    if balance > 0:
        return False
    await state.clear()
    await repo.set_fsm_state(pool, u.id, "screen:balance")
    await message.answer(
        texts.ask_need_payment(balance), reply_markup=keyboards.screen_nav()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: balance=0 в ходе диалога → оплата")
    return True


async def _safe_delete(msg: Message) -> None:
    """Удаляет служебное сообщение, не роняя поток при ошибке."""
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001 — удаление некритично
        pass

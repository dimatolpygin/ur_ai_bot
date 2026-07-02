"""Ветка «Задать вопрос» (этап 2): ответ ИИ + память диалога + списание.

Поток:
  «Задать вопрос» → если баланс 0, уводим в оплату; иначе входим в состояние
  ожидания вопроса. Текст в этом состоянии → вызов ИИ → при успехе списываем 1
  запрос (единственная точка расхода), пишем пару в память, показываем остаток.
  Ошибка/пустой ответ ИИ → баланс НЕ трогаем. «Новый диалог» — сброс памяти.

Списание строго после успешного ответа — критерий этапа. Кнопки навигации в этом
состоянии перехватываются раньше, чтобы их текст не ушёл в модель как вопрос.
"""
from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from redis.asyncio import Redis

from .. import ai, keyboards, memory, repo, texts
from ..logger import logger

router = Router()


class AskStates(StatesGroup):
    waiting_question = State()


# Ярлыки кнопок: в режиме ожидания их текст НЕ должен уходить в модель как вопрос,
# а должен работать как навигация (проваливается в menu-роутер / обработчики ниже).
_MENU_LABELS = {
    texts.BTN_ASK,
    texts.BTN_EMPLOYER,
    texts.BTN_BALANCE,
    texts.BTN_HELP,
    texts.BTN_MAIN_MENU,
    texts.BTN_NEW_DIALOG,
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


@router.message(
    AskStates.waiting_question,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(_MENU_LABELS),
)
async def handle_question(
    message: Message, pool: asyncpg.Pool, redis: Redis, state: FSMContext
) -> None:
    """Основной обработчик: текст в режиме ожидания = вопрос к ИИ."""
    u = message.from_user
    question = (message.text or "").strip()
    if not question:
        return

    # Повторная проверка баланса перед дорогой операцией (мог уйти в 0 параллельно).
    balance = await repo.get_balance(pool, u.id)
    if balance <= 0:
        await state.clear()
        await repo.set_fsm_state(pool, u.id, "screen:balance")
        await message.answer(
            texts.ask_need_payment(balance), reply_markup=keyboards.screen_nav()
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    status = await message.answer(texts.THINKING)

    history = await memory.get_history(redis, u.id)
    try:
        reply = await ai.answer(history, question)
    except ai.AIError as e:
        logger.warning(f"ИИ не ответил @{u.username or '—'}: {e} — баланс не списан")
        await _safe_delete(status)
        await message.answer(texts.AI_ERROR, reply_markup=keyboards.ask_screen())
        return

    # Успех → списываем ровно здесь (единственная точка расхода).
    new_balance = await repo.charge_one(pool, u.id)
    if new_balance is None:
        # Крайне редкая гонка: баланс исчерпан между проверкой и списанием.
        new_balance = 0
        logger.warning(f"@{u.username or '—'}: списание не прошло (баланс 0 в гонке)")

    await memory.append(redis, u.id, question, reply)
    await repo.add_event(pool, u.id, "Ответ ИИ выдан")

    await _safe_delete(status)
    await message.answer(
        reply + texts.answer_footer(new_balance), reply_markup=keyboards.ask_screen()
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: ответ ИИ выдан, баланс {new_balance}"
    )


async def _safe_delete(msg: Message) -> None:
    """Удаляет служебное сообщение «готовлю ответ», не роняя поток при ошибке."""
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001 — удаление некритично
        pass

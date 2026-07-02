"""Ветка «Проверить работодателя» (этап 5): шаблонный сценарий поверх агента поиска.

Поток:
  «Проверить работодателя» → при balance=0 уводим в оплату; иначе ждём ввод.
  Название / ИНН / ссылка → шаблонный промпт (`texts.employer_query`) уходит в тот
  же `ai.answer_with_search` (веб-поиск, этап 3) → сводка из открытых источников +
  ссылки + оговорка «данные из открытых источников». Списание 1 запроса — как
  обычный ответ, в единственной точке после успешной выдачи.

Навигация без тупиков: «Проверить другого» / «Задать вопрос» / «Главное меню».
Ветка изолирована от памяти диалога (это разовая проверка, не беседа).
"""
from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LinkPreviewOptions, Message

from .. import ai, keyboards, repo, texts
from ..logger import logger
from . import payment

router = Router()


class EmployerStates(StatesGroup):
    waiting_input = State()  # ждём название/ИНН/ссылку


# Ярлыки, которые в режиме ввода НЕ уходят в модель как «работодатель», а работают
# как навигация (проваливаются в свои роутеры: BTN_ASK → ask, BTN_MAIN_MENU → menu).
_STOP_LABELS = {
    texts.BTN_ASK,
    texts.BTN_EMPLOYER,
    texts.BTN_BALANCE,
    texts.BTN_HELP,
    texts.BTN_MAIN_MENU,
    texts.BTN_CHECK_ANOTHER,
}


@router.message(F.text == texts.BTN_EMPLOYER)
async def open_employer(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Вход в ветку из главного меню."""
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    if balance <= 0:
        await state.clear()
        await repo.set_fsm_state(pool, u.id, "screen:balance")
        await payment.send_balance_screen(message, pool)
        logger.info(f"🤖 Бот → @{u.username or '—'}: работодатель при balance=0 → оплата")
        return

    await state.set_state(EmployerStates.waiting_input)
    await repo.set_fsm_state(pool, u.id, "screen:employer")
    await message.answer(
        texts.employer_prompt(balance), reply_markup=keyboards.employer_input()
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: вход в «Проверить работодателя» (баланс {balance})"
    )


@router.message(EmployerStates.waiting_input, F.text == texts.BTN_CHECK_ANOTHER)
async def check_another(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """«Проверить другого» — снова просим ввод, остаёмся в ветке."""
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    if balance <= 0:
        await state.clear()
        await repo.set_fsm_state(pool, u.id, "screen:balance")
        await payment.send_balance_screen(message, pool)
        return
    await message.answer(
        texts.employer_prompt(balance), reply_markup=keyboards.employer_input()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: «Проверить другого» → ввод")


@router.message(
    EmployerStates.waiting_input,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(_STOP_LABELS),
)
async def handle_employer(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Ввод работодателя → веб-поиск по шаблону → сводка + ссылки + оговорка."""
    u = message.from_user
    query = (message.text or "").strip()
    if not query:
        await message.answer(
            texts.EMPLOYER_EMPTY_INPUT, reply_markup=keyboards.employer_input()
        )
        return

    # Повторная проверка баланса перед дорогой операцией (мог уйти в 0 параллельно).
    balance = await repo.get_balance(pool, u.id)
    if balance <= 0:
        await state.clear()
        await repo.set_fsm_state(pool, u.id, "screen:balance")
        await payment.send_balance_screen(message, pool)
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    status = await message.answer(texts.THINKING)

    async def notify(text: str) -> None:
        """Обновляет статус поиска в одном сообщении («не молчим», §5.3)."""
        try:
            await status.edit_text(text)
        except Exception:  # noqa: BLE001 — совпадающий текст/сеть не критичны
            pass

    # Изолированный сценарий: без памяти диалога, только шаблонный запрос.
    try:
        reply, sources = await ai.answer_with_search(
            [], texts.employer_query(query), notify
        )
    except ai.AIError as e:
        logger.warning(f"Проверка работодателя не удалась @{u.username or '—'}: {e}")
        await _safe_delete(status)
        await message.answer(texts.AI_ERROR, reply_markup=keyboards.employer_result())
        return

    # Успех → списываем 1 запрос (как обычный ответ), единственная точка расхода.
    new_balance = await repo.charge_one(pool, u.id)
    if new_balance is None:
        new_balance = 0
        logger.warning(f"@{u.username or '—'}: списание не прошло (баланс 0 в гонке)")

    await repo.add_event(pool, u.id, "Проверка работодателя")

    await _safe_delete(status)
    await message.answer(
        reply
        + texts.sources_block(sources)
        + texts.EMPLOYER_DISCLAIMER
        + texts.answer_footer(new_balance),
        reply_markup=keyboards.employer_result(),
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: сводка по работодателю «{query[:40]}» "
        f"(источников {len(sources)}), баланс {new_balance}"
    )


async def _safe_delete(msg: Message) -> None:
    """Удаляет служебное сообщение, не роняя поток при ошибке."""
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001 — удаление некритично
        pass

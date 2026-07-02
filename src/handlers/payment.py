"""Ветка «Баланс и оплата» (этап 6): покупка пакетов запросов через ЮKassa.

Поток:
  «Баланс и оплата» → экран с остатком, ценами пакетов и историей покупок +
  inline-кнопки пакетов 10/20/30. Тап по пакету → счёт ЮKassa с кнопками
  «Оплатить» (ссылка) и «Проверить оплату». Домена нет → без вебхука: статус
  подтягивает кнопка «Проверить» и фоновый поллер (APScheduler). Зачисление
  balance += N — единственная идемпотентная точка `repo.credit_payment`.

`send_balance_screen` переиспользуется ветками вопроса/работодателя, когда у юзера
кончились запросы (balance=0 → сюда, в оплату).
"""
from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards, payments, repo, settings_repo, texts
from ..logger import logger
from ..yookassa import YooKassaError

router = Router()


async def send_balance_screen(message: Message, pool: asyncpg.Pool) -> None:
    """Показывает экран баланса + цены + историю + inline-кнопки пакетов.

    Двумя сообщениями: инфо-экран (reply-навигация «Главное меню») и выбор пакета
    (inline). Общая точка для ветки оплаты и для тупика balance=0 в других ветках.
    """
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    prices = await settings_repo.package_prices(pool)
    history = await repo.get_user_payments(pool, u.id, limit=5)
    await message.answer(
        texts.balance_screen(balance, prices, history),
        reply_markup=keyboards.screen_nav(),
    )
    if prices:
        await message.answer(
            texts.CHOOSE_PACKAGE, reply_markup=keyboards.packages_kb(prices)
        )


@router.message(F.text == texts.BTN_BALANCE)
async def open_balance(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Вход в раздел из главного меню."""
    u = message.from_user
    await state.clear()
    await repo.set_fsm_state(pool, u.id, "screen:balance")
    await send_balance_screen(message, pool)
    balance = await repo.get_balance(pool, u.id)
    logger.info(f"🤖 Бот → @{u.username or '—'}: экран «Баланс и оплата» ({balance})")


@router.callback_query(F.data.startswith(keyboards.CB_BUY))
async def buy_package(callback: CallbackQuery, pool: asyncpg.Pool) -> None:
    """Тап по пакету → создаём счёт ЮKassa, отдаём кнопки оплаты/проверки."""
    u = callback.from_user
    try:
        package = int(callback.data[len(keyboards.CB_BUY):])
    except ValueError:
        await callback.answer()
        return

    try:
        data = await payments.start_payment(pool, tg_id=u.id, package=package)
    except YooKassaError as e:
        logger.warning(f"ЮKassa не создала платёж @{u.username or '—'}: {e}")
        await callback.message.answer(texts.PAY_ERROR)
        await callback.answer()
        return

    if data is None or not data.get("confirmation_url"):
        await callback.message.answer(texts.PAY_ERROR)
        await callback.answer()
        return

    await callback.message.answer(
        texts.pay_created(package, data["amount"]),
        reply_markup=keyboards.payment_actions_kb(
            data["confirmation_url"], data["payment_id"]
        ),
    )
    await callback.answer()
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: счёт на пакет {package} ({data['amount']} ₽), "
        f"yk_id={data['payment_id']}"
    )


@router.callback_query(F.data.startswith(keyboards.CB_CHECK))
async def check_payment(callback: CallbackQuery, pool: asyncpg.Pool) -> None:
    """«Проверить оплату» → синхронизация статуса; зачисление при succeeded."""
    u = callback.from_user
    yk_id = callback.data[len(keyboards.CB_CHECK):]
    row = await repo.get_payment_by_yk_id(pool, yk_id)
    if row is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    status = await payments.sync_payment(pool, callback.bot, row)
    if status == "succeeded":
        # Уведомление об успехе шлёт sync_payment (единый источник). Здесь только
        # убираем кнопки счёта, чтобы повторно не жали.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 — сообщение могло измениться
            pass
        await callback.answer("Оплата прошла — запросы зачислены")
    elif status == "canceled":
        try:
            await callback.message.edit_text(texts.PAY_CANCELED)
        except Exception:  # noqa: BLE001
            pass
        await callback.answer("Платёж отменён")
    else:
        await callback.answer(texts.PAY_PENDING, show_alert=True)
    logger.info(f"🤖 Бот → @{u.username or '—'}: проверка оплаты yk_id={yk_id} → {status}")


@router.callback_query(F.data.startswith(keyboards.CB_CANCEL))
async def cancel_payment(callback: CallbackQuery, pool: asyncpg.Pool) -> None:
    """«Отмена» счёта: помечаем платёж отменённым, возвращаем выбор пакета."""
    u = callback.from_user
    yk_id = callback.data[len(keyboards.CB_CANCEL):]
    await repo.mark_payment_canceled(pool, yk_id)
    try:
        await callback.message.edit_text(texts.PAY_CANCELED)
    except Exception:  # noqa: BLE001
        pass
    prices = await settings_repo.package_prices(pool)
    if prices:
        await callback.message.answer(
            texts.CHOOSE_PACKAGE, reply_markup=keyboards.packages_kb(prices)
        )
    await callback.answer("Отменено")
    logger.info(f"🤖 Бот → @{u.username or '—'}: отмена счёта yk_id={yk_id}")

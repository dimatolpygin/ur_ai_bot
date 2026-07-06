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

import re

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards, payments, repo, settings_repo, texts
from ..logger import logger
from ..yookassa import YooKassaError

router = Router()


class PaymentStates(StatesGroup):
    waiting_email = State()  # ждём email для чека перед первой покупкой


# Простая проверка email: что-то@что-то.домен, без пробелов. Точную валидность
# всё равно проверит ЮKassa при отправке чека.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _send_invoice(
    message: Message, pool: asyncpg.Pool, u, package: int
) -> None:
    """Создаёт счёт ЮKassa под пакет и отдаёт кнопки оплаты/проверки.

    Общая точка: вызывается сразу (если email уже есть) и после ввода email.
    """
    try:
        data = await payments.start_payment(pool, tg_id=u.id, package=package)
    except YooKassaError as e:
        logger.warning(f"ЮKassa не создала платёж @{u.username or '—'}: {e}")
        await message.answer(texts.PAY_ERROR)
        return

    if data is None or not data.get("confirmation_url"):
        await message.answer(texts.PAY_ERROR)
        return

    await message.answer(
        texts.pay_created(package, data["amount"]),
        reply_markup=keyboards.payment_actions_kb(
            data["confirmation_url"], data["payment_id"]
        ),
    )
    logger.info(
        f"🤖 Бот → @{u.username or '—'}: счёт на пакет {package} ({data['amount']} ₽), "
        f"yk_id={data['payment_id']}"
    )


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
async def buy_package(
    callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Тап по пакету. Нет сохранённого email → сначала спрашиваем его (для чека),
    иначе сразу создаём счёт ЮKassa."""
    u = callback.from_user
    try:
        package = int(callback.data[len(keyboards.CB_BUY):])
    except ValueError:
        await callback.answer()
        return

    email = await repo.get_email(pool, u.id)
    if not email:
        await state.set_state(PaymentStates.waiting_email)
        await state.update_data(pending_package=package)
        await callback.message.answer(texts.ASK_EMAIL, reply_markup=keyboards.email_input())
        await callback.answer()
        logger.info(f"🤖 Бот → @{u.username or '—'}: прошу email перед покупкой пакета {package}")
        return

    await _send_invoice(callback.message, pool, u, package)
    await callback.answer()


@router.message(PaymentStates.waiting_email, F.text == texts.BTN_MAIN_MENU)
async def email_to_menu(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Выход из ввода email в главное меню (без списаний, без тупиков)."""
    u = message.from_user
    await state.clear()
    balance = await repo.get_balance(pool, u.id)
    await repo.set_fsm_state(pool, u.id, "screen:main_menu")
    await message.answer(
        texts.welcome_back(u.first_name, balance), reply_markup=keyboards.main_menu()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: отмена ввода email → главное меню")


@router.message(PaymentStates.waiting_email, F.text)
async def receive_email(
    message: Message, pool: asyncpg.Pool, state: FSMContext
) -> None:
    """Приняли email: валидируем, сохраняем, продолжаем к счёту за отложенный пакет."""
    u = message.from_user
    email = (message.text or "").strip()
    if not _EMAIL_RE.match(email):
        await message.answer(texts.EMAIL_INVALID, reply_markup=keyboards.email_input())
        logger.info(f"🤖 Бот → @{u.username or '—'}: невалидный email, переспрашиваю")
        return

    await repo.set_email(pool, u.id, email)
    data = await state.get_data()
    package = data.get("pending_package")
    await state.clear()
    await message.answer(texts.email_saved(email))
    logger.info(f"🤖 Бот → @{u.username or '—'}: email сохранён ({email}), пакет {package}")

    if package:
        await _send_invoice(message, pool, u, package)
    else:
        await send_balance_screen(message, pool)


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

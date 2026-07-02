"""Оркестрация платежей ЮKassa: создание счёта и зачисление пакета запросов.

Поток:
  1. start_payment — взять цену пакета из app_settings, создать платёж в ЮKassa,
     сохранить строку payments (pending), вернуть ссылку на оплату.
  2. sync_payment — узнать статус платежа; при succeeded атомарно пополнить баланс
     (repo.credit_payment — без дублей) и уведомить пользователя ровно один раз;
     при canceled — пометить платёж отменённым.
sync_payment дёргается и фоновым поллером, и кнопкой «Проверить оплату».
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg
from aiogram import Bot

from . import repo, settings_repo, texts
from .config import settings
from .logger import logger
from .receipt import build_receipt
from .yookassa import YooKassaError, create_payment, get_payment, new_idempotence_key


async def start_payment(
    pool: asyncpg.Pool, *, tg_id: int, package: int
) -> dict | None:
    """Создаёт платёж ЮKassa под пакет запросов. None — если пакет/цена неизвестны."""
    amount = await settings_repo.package_price(pool, package)
    if amount is None or amount <= 0:
        return None

    description = f"Пакет {package} запросов — юридический помощник URIST2026"
    receipt = build_receipt(description, amount)
    idem = new_idempotence_key()
    metadata = {"tg_id": str(tg_id), "package": str(package)}

    payment = await create_payment(
        amount=amount,
        description=description,
        return_url=settings.yookassa_return_url,
        metadata=metadata,
        receipt=receipt,
        idempotence_key=idem,
    )
    yk_id = payment["id"]
    confirmation_url = (payment.get("confirmation") or {}).get("confirmation_url")
    await repo.create_payment(
        pool,
        yookassa_payment_id=yk_id,
        idempotence_key=idem,
        tg_id=tg_id,
        package=package,
        amount=amount,
        confirmation_url=confirmation_url,
        status=payment.get("status", "pending"),
    )
    logger.info(
        f"💳 Платёж создан: tg_id={tg_id}, пакет {package} за {amount} ₽, yk_id={yk_id}"
    )
    return {
        "payment_id": yk_id,
        "confirmation_url": confirmation_url,
        "amount": amount,
        "package": package,
    }


async def sync_payment(
    pool: asyncpg.Pool, bot: Bot, payment: asyncpg.Record
) -> str:
    """Сверяет один платёж с ЮKassa. Возвращает статус: succeeded / pending / canceled.

    При первом успешном зачислении шлёт пользователю уведомление (ровно один раз).
    """
    yk_id = payment["yookassa_payment_id"]

    # Уже финализирован в нашей БД — без обращения к API.
    if payment["status"] == "succeeded":
        return "succeeded"
    if payment["status"] == "canceled":
        return "canceled"

    try:
        data = await get_payment(yk_id)
    except YooKassaError:
        return "pending"  # сеть недоступна — попробуем в следующий раз

    status = data.get("status")
    if status == "succeeded":
        new_balance, credited_now = await repo.credit_payment(pool, yk_id)
        if new_balance is None:
            return "pending"  # платёж не нашёлся (крайне маловероятно)
        if credited_now:
            logger.info(
                f"✅ Пакет {payment['package']} зачислен (tg_id={payment['tg_id']}, "
                f"yk_id={yk_id}), баланс {new_balance}"
            )
            await repo.add_event(pool, payment["tg_id"], "Оплата зачислена")
            await _notify_success(bot, payment["tg_id"], payment["package"], new_balance)
        return "succeeded"

    if status in ("canceled", "cancelled"):
        await repo.mark_payment_canceled(pool, yk_id)
        logger.info(f"🚫 Платёж отменён: tg_id={payment['tg_id']}, yk_id={yk_id}")
        return "canceled"

    return "pending"


async def _notify_success(
    bot: Bot, tg_id: int, package: int, new_balance: int
) -> None:
    from . import keyboards  # локальный импорт — избегаем цикла на старте

    try:
        await bot.send_message(
            tg_id,
            texts.pay_success(package, new_balance),
            reply_markup=keyboards.main_menu(),
        )
    except Exception as e:  # noqa: BLE001 — пользователь мог заблокировать бота
        logger.warning(f"Не удалось уведомить tg_id={tg_id} об оплате: {e}")


async def poll_pending(pool: asyncpg.Pool, bot: Bot) -> None:
    """Фоновый проход поллера по всем pending-платежам (вебхука нет)."""
    pending = await repo.get_pending_payments(pool)
    if not pending:
        return
    logger.info(f"🔄 Поллер: проверяю {len(pending)} pending-платеж(ей)")
    for p in pending:
        try:
            await sync_payment(pool, bot, p)
        except Exception as e:  # noqa: BLE001 — один платёж не должен ронять цикл
            logger.error(f"Поллер: ошибка по yk_id={p['yookassa_payment_id']}: {e}")

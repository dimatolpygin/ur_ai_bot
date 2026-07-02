"""Админка `/admin` (этап 7): управление ценами и ключами поиска из бота.

Доступ — только для id из `settings.admin_id_list` (`ADMIN_IDS` в .env); чужим отказ
и на команду, и на callback'и. Всё живёт в одном inline-сообщении, которое
перерисовывается: шапка + меню → экран ввода значения (с «Отмена») → снова меню.

Что можно менять (всё горячо, без рестарта — значения в `app_settings`):
  · цены пакетов 10/20/30 (RUB) — применяются на экране «Баланс и оплата»;
  · цену одного запроса (единиц баланса за ответ) — учитывается в `repo.charge_one`;
  · API-ключи поисковиков Tavily/Exa/Firecrawl — переопределяют .env, берутся
    `search.run_web_search` со следующего поиска.

Шапка также показывает статистику юзеров и балансы сервисов: реальный остаток
кредитов OpenRouter (`GET /credits`) и собственный счётчик расхода поисковиков.
Навигация без тупиков: у ввода — «Отмена», у меню — «Обновить»/«Закрыть»,
а /start сбрасывает FSM (запасной выход).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import ai, keyboards, repo, settings_repo, texts
from ..config import settings
from ..logger import logger

router = Router()


class AdminStates(StatesGroup):
    waiting_value = State()  # ждём новое значение (цена/ключ), тип — в data["kind"]


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list


async def _header_text(pool: asyncpg.Pool) -> str:
    """Собирает шапку админки: статистика, балансы, текущие цены и ключи."""
    counts = await repo.user_counts(pool)
    credits = await ai.account_credits()
    usage = await settings_repo.search_usage(pool)
    prices = await settings_repo.package_prices(pool)
    ppr = await settings_repo.price_per_request(pool)
    keys = {p: await settings_repo.search_key(pool, p) for p in settings_repo.SEARCH_PROVIDERS}
    return texts.admin_header(counts, credits, usage, prices, ppr, keys)


@router.message(Command("admin"))
async def cmd_admin(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    u = message.from_user
    if not _is_admin(u.id):
        await message.answer(texts.ADMIN_DENIED)
        logger.warning(f"⛔ Отказ /admin @{u.username or '—'} (id:{u.id}) — не админ")
        return
    await state.clear()
    await message.answer(
        await _header_text(pool), reply_markup=keyboards.admin_menu_kb()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: открыл админку")


async def _guard_cb(callback: CallbackQuery) -> bool:
    """True = доступ есть. Иначе отвечает отказом и гасит «часики»."""
    if _is_admin(callback.from_user.id):
        return True
    await callback.answer(texts.ADMIN_DENIED, show_alert=True)
    logger.warning(
        f"⛔ Отказ admin-callback @{callback.from_user.username or '—'} "
        f"(id:{callback.from_user.id}) — не админ"
    )
    return False


async def _show_menu(callback: CallbackQuery, pool: asyncpg.Pool) -> None:
    """Перерисовывает текущее сообщение в шапку + меню админки."""
    try:
        await callback.message.edit_text(
            await _header_text(pool), reply_markup=keyboards.admin_menu_kb()
        )
    except Exception:  # noqa: BLE001 — «message is not modified» и сетевые не критичны
        pass


@router.callback_query(F.data == keyboards.CB_ADM_REFRESH)
async def adm_refresh(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    await state.clear()
    await _show_menu(callback, pool)
    await callback.answer("Обновлено")


@router.callback_query(F.data == keyboards.CB_ADM_CLOSE)
async def adm_close(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    await state.clear()
    try:
        await callback.message.edit_text(texts.ADMIN_CLOSED)
    except Exception:  # noqa: BLE001
        pass
    await callback.answer()
    logger.info(f"🤖 Бот → @{callback.from_user.username or '—'}: закрыл админку")


@router.callback_query(F.data == keyboards.CB_ADM_CANCEL)
async def adm_cancel(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    await state.clear()
    await _show_menu(callback, pool)
    await callback.answer(texts.ADMIN_EDIT_CANCELED)


async def _enter_edit(
    callback: CallbackQuery, state: FSMContext, prompt: str, data: dict
) -> None:
    """Переводит текущее сообщение в экран ввода значения и включает FSM-ожидание."""
    try:
        await callback.message.edit_text(prompt, reply_markup=keyboards.admin_cancel_kb())
    except Exception:  # noqa: BLE001
        pass
    # Запоминаем сообщение меню, чтобы вернуть его в шапку после ввода значения.
    data["chat_id"] = callback.message.chat.id
    data["msg_id"] = callback.message.message_id
    await state.set_state(AdminStates.waiting_value)
    await state.set_data(data)
    await callback.answer()


@router.callback_query(F.data.startswith(keyboards.CB_ADM_PKG))
async def adm_edit_pkg(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    try:
        package = int(callback.data[len(keyboards.CB_ADM_PKG):])
    except ValueError:
        await callback.answer()
        return
    current = await settings_repo.package_price(pool, package)
    await _enter_edit(
        callback, state,
        texts.admin_ask_price(package, current),
        {"kind": "price", "package": package},
    )


@router.callback_query(F.data == keyboards.CB_ADM_PPR)
async def adm_edit_ppr(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    current = await settings_repo.price_per_request(pool)
    await _enter_edit(
        callback, state, texts.admin_ask_ppr(current), {"kind": "ppr"}
    )


@router.callback_query(F.data.startswith(keyboards.CB_ADM_KEY))
async def adm_edit_key(callback: CallbackQuery, pool: asyncpg.Pool, state: FSMContext) -> None:
    if not await _guard_cb(callback):
        return
    provider = callback.data[len(keyboards.CB_ADM_KEY):]
    if provider not in settings_repo.SEARCH_PROVIDERS:
        await callback.answer()
        return
    current = await settings_repo.search_key(pool, provider)
    await _enter_edit(
        callback, state,
        texts.admin_ask_key(provider, current),
        {"kind": "key", "provider": provider},
    )


@router.message(AdminStates.waiting_value, F.text)
async def adm_receive_value(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    """Приём нового значения (цена/ключ), валидация, сохранение, возврат в меню."""
    u = message.from_user
    if not _is_admin(u.id):  # состояние админское, но подстрахуемся
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("kind")
    raw = (message.text or "").strip()

    if kind == "price":
        package = int(data["package"])
        value = _parse_price(raw)
        if value is None:
            await message.answer(texts.ADMIN_BAD_PRICE)
            return
        await settings_repo.set_value(pool, settings_repo._PKG_KEY[package], str(value))
        confirm = texts.admin_saved_price(package, value)
        logger.info(f"🤖 Админка @{u.username or '—'}: цена пакета {package} → {value} ₽")

    elif kind == "ppr":
        value = _parse_int(raw)
        if value is None:
            await message.answer(texts.ADMIN_BAD_PPR)
            return
        await settings_repo.set_value(pool, "price_per_request", str(value))
        confirm = texts.admin_saved_ppr(value)
        logger.info(f"🤖 Админка @{u.username or '—'}: цена запроса → {value}")

    elif kind == "key":
        provider = data["provider"]
        if not raw:
            await message.answer(texts.ADMIN_BAD_KEY)
            return
        await settings_repo.set_search_key(pool, provider, raw)
        confirm = texts.admin_saved_key(provider)
        logger.info(f"🤖 Админка @{u.username or '—'}: обновлён ключ {provider} (····{raw[-4:]})")

    else:  # неизвестный тип — просто выходим в меню
        await state.clear()
        await message.answer(await _header_text(pool), reply_markup=keyboards.admin_menu_kb())
        return

    await state.clear()
    # Возвращаем сообщение-меню в актуальную шапку (значения уже применены).
    await _refresh_menu_message(message, pool, data)
    await message.answer(confirm)


async def _refresh_menu_message(message: Message, pool: asyncpg.Pool, data: dict) -> None:
    """Перерисовывает запомненное сообщение-меню в свежую шапку; если не вышло —
    шлёт меню новым сообщением (без тупиков в любом случае)."""
    chat_id = data.get("chat_id")
    msg_id = data.get("msg_id")
    header = await _header_text(pool)
    if chat_id and msg_id:
        try:
            await message.bot.edit_message_text(
                header, chat_id=chat_id, message_id=msg_id,
                reply_markup=keyboards.admin_menu_kb(),
            )
            return
        except Exception:  # noqa: BLE001 — сообщение могло устареть/не измениться
            pass
    await message.answer(header, reply_markup=keyboards.admin_menu_kb())


def _parse_price(raw: str) -> Decimal | None:
    """Цена в рублях: положительное число. None — если не разобрать/не положительное."""
    try:
        value = Decimal(raw.replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _parse_int(raw: str) -> int | None:
    """Целое ≥ 1 (цена запроса). None — если не разобрать/меньше 1."""
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value >= 1 else None

"""Ветки главного меню (этап 1 — экраны-заглушки).

Каждая кнопка ведёт на свой экран; на каждом экране есть выход «Главное меню» —
тупиков нет. Реальная логика веток появится на следующих этапах (вопрос — этап 2,
работодатель — этап 5, оплата — этап 6, помощь — этап 8).
"""
from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards, repo, texts
from ..logger import logger

router = Router()


async def _mark(pool: asyncpg.Pool, tg_id: int, screen: str) -> None:
    """Фиксируем, на каком экране пользователь (задел под аналитику пути)."""
    await repo.set_fsm_state(pool, tg_id, f"screen:{screen}")


@router.message(F.text == texts.BTN_EMPLOYER)
async def open_employer(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    await _mark(pool, u.id, "employer")
    await message.answer(texts.screen_employer(), reply_markup=keyboards.screen_nav())
    logger.info(f"🤖 Бот → @{u.username or '—'}: экран «Проверить работодателя»")


@router.message(F.text == texts.BTN_BALANCE)
async def open_balance(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    await _mark(pool, u.id, "balance")
    await message.answer(
        texts.screen_balance(balance), reply_markup=keyboards.screen_nav()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: экран «Баланс и оплата» ({balance})")


@router.message(F.text == texts.BTN_HELP)
async def open_help(message: Message, pool: asyncpg.Pool) -> None:
    u = message.from_user
    await _mark(pool, u.id, "help")
    await message.answer(texts.screen_help(), reply_markup=keyboards.screen_nav())
    logger.info(f"🤖 Бот → @{u.username or '—'}: экран «Помощь»")


@router.message(F.text == texts.BTN_MAIN_MENU)
async def back_to_menu(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    """Выход из любого экрана — возврат в главное меню."""
    await state.clear()
    u = message.from_user
    balance = await repo.get_balance(pool, u.id)
    await _mark(pool, u.id, "main_menu")
    await message.answer(
        texts.welcome_back(u.first_name, balance), reply_markup=keyboards.main_menu()
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: возврат в главное меню")


@router.message(F.text)
async def fallback(message: Message, pool: asyncpg.Pool) -> None:
    """Любой нераспознанный текст: не оставляем без ответа — возвращаем в меню.

    Свободный ввод вопросов подключится на этапе 2; пока направляем к кнопкам.
    """
    u = message.from_user
    await message.answer(
        "Пока я работаю через кнопки меню ниже. Выберите нужный раздел.",
        reply_markup=keyboards.main_menu(),
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: фолбэк, нераспознанный ввод")

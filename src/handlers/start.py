"""Хендлеры /start и /id — вход в бота.

Этап 1: новичку показываем приветствие + дисклеймер и начисляем N бесплатных
запросов; вернувшемуся — короткое приветствие. Обоим показываем главное меню.
"""
from __future__ import annotations

import asyncpg
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards, repo, texts
from ..config import settings
from ..logger import logger

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    is_new, balance = await repo.upsert_user(
        pool, u.id, u.username, u.first_name, settings.free_requests_on_start
    )
    await repo.set_fsm_state(pool, u.id, "screen:main_menu")

    if is_new:
        text = texts.welcome_new(u.first_name, balance)
        await repo.log_event(pool, u.id, repo.EVENT_REGISTER, {"free": balance})
        logger.info(
            f"🤖 Бот → @{u.username or '—'}: онбординг нового юзера, "
            f"начислено {balance} запросов"
        )
    else:
        text = texts.welcome_back(u.first_name, balance)
        logger.info(f"🤖 Бот → @{u.username or '—'}: возврат, баланс {balance}")

    await message.answer(text, reply_markup=keyboards.main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    u = message.from_user
    await message.answer(f"Ваш Telegram ID: <code>{u.id}</code>")
    logger.info(f"🤖 Бот → @{u.username or '—'}: /id ({u.id})")

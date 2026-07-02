"""Хендлеры /start и /id. Точка входа в бота.

Этап 0 — заглушка: подтверждаем, что бот жив, регистрируем пользователя в БД.
Приветствие, дисклеймер и главное меню появятся на этапе 1.
"""
from __future__ import annotations

import asyncpg
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import repo
from ..logger import logger

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    await repo.upsert_user(pool, u.id, u.username, u.first_name)
    await repo.set_fsm_state(pool, u.id, "screen:start")
    await message.answer(
        "<b>Юр-бот на связи.</b>\n\n"
        "Каркас запущен (этап 0). Приветствие, дисклеймер и меню — на следующем этапе."
    )
    logger.info(f"🤖 Бот → @{u.username or '—'}: заглушка /start")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    u = message.from_user
    await message.answer(f"Ваш Telegram ID: <code>{u.id}</code>")
    logger.info(f"🤖 Бот → @{u.username or '—'}: /id ({u.id})")

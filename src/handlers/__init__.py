"""Сбор всех роутеров. Порядок важен: команды → ветка вопроса (FSM) → меню, фолбэк последним."""
from aiogram import Router

from . import ask, menu, start


def get_main_router() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(ask.router)
    router.include_router(menu.router)
    return router

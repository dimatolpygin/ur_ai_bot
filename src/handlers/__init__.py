"""Сбор всех роутеров. Порядок важен: сначала команды и кнопки меню, фолбэк — последним."""
from aiogram import Router

from . import menu, start


def get_main_router() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(menu.router)
    return router

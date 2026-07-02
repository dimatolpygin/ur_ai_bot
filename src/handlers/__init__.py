"""Сбор всех роутеров. Порядок важен: команды → ветки-FSM (вопрос, работодатель) → меню, фолбэк последним."""
from aiogram import Router

from . import ask, employer, menu, start


def get_main_router() -> Router:
    router = Router()
    router.include_router(start.router)
    router.include_router(ask.router)
    router.include_router(employer.router)
    router.include_router(menu.router)
    return router

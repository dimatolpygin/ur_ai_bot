"""Reply-клавиатуры. Большие кнопки снизу — под аудиторию 35–50 лет.

Меню плоское (глубина 1): с любого экрана-заглушки есть выход «Главное меню»,
поэтому тупиков нет. Названия кнопок берём из texts.py, чтобы клавиатура и фильтры
хендлеров не разъезжались.
"""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from . import texts


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню: 4 ветки. Две кнопки в ряд — компактно и читаемо."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_ASK)],
            [KeyboardButton(text=texts.BTN_EMPLOYER)],
            [
                KeyboardButton(text=texts.BTN_BALANCE),
                KeyboardButton(text=texts.BTN_HELP),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел или напишите вопрос",
    )


def screen_nav() -> ReplyKeyboardMarkup:
    """Клавиатура экрана-заглушки: единственный выход — «Главное меню» (без тупиков)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_MAIN_MENU)]],
        resize_keyboard=True,
    )


def ask_screen() -> ReplyKeyboardMarkup:
    """Клавиатура ветки вопроса: сброс контекста и выход в меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_NEW_DIALOG)],
            [KeyboardButton(text=texts.BTN_MAIN_MENU)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напишите вопрос одним сообщением",
    )

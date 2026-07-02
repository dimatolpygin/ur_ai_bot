"""Reply-клавиатуры. Большие кнопки снизу — под аудиторию 35–50 лет.

Меню плоское (глубина 1): с любого экрана-заглушки есть выход «Главное меню»,
поэтому тупиков нет. Названия кнопок берём из texts.py, чтобы клавиатура и фильтры
хендлеров не разъезжались.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from . import texts

# Префиксы callback_data ветки оплаты (этап 6).
CB_BUY = "buy:"  # buy:<package>
CB_CHECK = "check:"  # check:<yookassa_payment_id>
CB_CANCEL = "cancel:"  # cancel:<yookassa_payment_id>


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


def employer_input() -> ReplyKeyboardMarkup:
    """Экран ввода работодателя: единственный выход — «Главное меню» (без тупиков)."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_MAIN_MENU)]],
        resize_keyboard=True,
        input_field_placeholder="Название, ИНН или ссылка одним сообщением",
    )


def employer_result() -> ReplyKeyboardMarkup:
    """Экран после сводки: проверить ещё, перейти к вопросам или в меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_CHECK_ANOTHER)],
            [
                KeyboardButton(text=texts.BTN_ASK),
                KeyboardButton(text=texts.BTN_MAIN_MENU),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Проверить другого — название, ИНН или ссылка",
    )


def packages_kb(prices: dict) -> InlineKeyboardMarkup:
    """Inline-кнопки покупки пакетов (по строке на пакет): «Пакет N · X ₽»."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"Пакет {pkg} · {texts._fmt_price(prices[pkg])} ₽",
                callback_data=f"{CB_BUY}{pkg}",
            )
        ]
        for pkg in sorted(prices)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_actions_kb(confirmation_url: str, yk_id: str) -> InlineKeyboardMarkup:
    """Кнопки счёта: оплатить (URL), проверить оплату, отмена."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=confirmation_url)],
            [
                InlineKeyboardButton(
                    text="Проверить оплату", callback_data=f"{CB_CHECK}{yk_id}"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data=f"{CB_CANCEL}{yk_id}")],
        ]
    )


def collecting_kb(quick_replies: list[str]) -> ReplyKeyboardMarkup:
    """Клавиатура сбора ситуации (этап 4): варианты ответа + escape-кнопки.

    quick_replies — подсказки от служебной модели (каждая своей строкой, чтобы
    крупно читались). Всегда есть выход: «Ответить сейчас» (оборвать сбор) и
    «Отмена» (в главное меню) — тупиков нет.
    """
    rows = [[KeyboardButton(text=qr)] for qr in quick_replies[:4]]
    rows.append([KeyboardButton(text=texts.BTN_ANSWER_NOW)])
    rows.append([KeyboardButton(text=texts.BTN_CANCEL)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Ответьте кнопкой или напишите своими словами",
    )

"""Чек 54-ФЗ для платежа ЮKassa — единая точка сборки.

Email покупателя пока не собираем у пользователя — подставляем заглушку из
настроек (receipt_email_placeholder). Когда понадобится реальный email —
достаточно начать сохранять его в users.email и подхватить здесь.
"""
from __future__ import annotations

from decimal import Decimal

from .config import settings


def build_receipt(description: str, amount: Decimal) -> dict:
    """Чек из одной позиции (пакет запросов). vat_code=1 — без НДС (УСН)."""
    return {
        "customer": {"email": settings.receipt_email_placeholder},
        "items": [
            {
                "description": description,
                "quantity": "1.00",
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_subject": "service",
                "payment_mode": "full_prepayment",
            }
        ],
    }

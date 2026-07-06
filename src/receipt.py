"""Чек 54-ФЗ для платежа ЮKassa — единая точка сборки.

Email покупателя спрашиваем перед первой покупкой и храним в users.email; сюда он
приходит параметром (см. payments.start_payment). Если по какой-то причине пусто —
подставляем заглушку из настроек (receipt_email_placeholder).

vat_code=1 — «Без НДС» (ИП на УСН «доходы» без НДС). tax_system_code не
передаём: у магазина одна система налогообложения, касса подставит сама. Если у
магазина ЕСТЬ НДС или НЕСКОЛЬКО систем — поменять vat_code (2/3/4) и добавить
tax_system_code (напр. 2 — УСН доход) по режиму ИП.
"""
from __future__ import annotations

from decimal import Decimal


def build_receipt(description: str, amount: Decimal, email: str) -> dict:
    """Чек из одной позиции (пакет запросов). email — на него уйдёт чек."""
    return {
        "customer": {"email": email},
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

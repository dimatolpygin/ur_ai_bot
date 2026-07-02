"""Тонкий async-клиент ЮKassa (REST API v3, без вебхука).

Две операции:
  · create_payment — создать платёж, получить confirmation_url;
  · get_payment    — узнать текущий статус (для polling и кнопки «Проверить»).

Авторизация — HTTP Basic (shop_id:secret_key). Создание платежа требует заголовок
Idempotence-Key (UUID) — повтор с тем же ключом не плодит платежи. aiohttp идёт
зависимостью aiogram, отдельной строки в requirements не нужно.
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import aiohttp

from .config import settings
from .logger import logger

_API_URL = "https://api.yookassa.ru/v3/payments"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


class YooKassaError(RuntimeError):
    """Ошибка обращения к API ЮKassa (сеть/HTTP/невалидный ответ)."""


def _auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(settings.yookassa_shop_id, settings.yookassa_secret_key)


def new_idempotence_key() -> str:
    return str(uuid.uuid4())


async def _request(method: str, url: str, **kwargs) -> tuple[int, dict]:
    """Запрос к ЮKassa, при необходимости через прокси (RU-выход для прод-сервера).

    settings.yookassa_proxy: пусто — напрямую; http(s)://… — обычный HTTP-прокси
    (aiohttp нативно); socks5://… — через aiohttp_socks (ленивый импорт).
    asyncio.TimeoutError ловим явно — он НЕ подкласс aiohttp.ClientError.
    """
    proxy = settings.yookassa_proxy or None
    connector = None
    if proxy and proxy.lower().startswith("socks"):
        from aiohttp_socks import ProxyConnector  # ленивый импорт — только для socks

        connector = ProxyConnector.from_url(proxy)
    elif proxy:
        kwargs["proxy"] = proxy
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector) as session:
            async with session.request(method, url, auth=_auth(), **kwargs) as resp:
                return resp.status, await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"ЮKassa {method} {url} сеть/таймаут: {e!r}")
        raise YooKassaError("Сеть недоступна при обращении к ЮKassa") from e


async def create_payment(
    *,
    amount: Decimal,
    description: str,
    return_url: str,
    metadata: dict,
    receipt: dict,
    idempotence_key: str,
) -> dict:
    """Создаёт платёж с capture=true (списывается сразу при подтверждении).

    Возвращает JSON платежа (id, status, confirmation.confirmation_url…).
    """
    body = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description,
        "metadata": metadata,
        "receipt": receipt,
    }
    headers = {"Idempotence-Key": idempotence_key}
    status, data = await _request("POST", _API_URL, json=body, headers=headers)
    if status not in (200, 201):
        logger.error(f"ЮKassa create_payment HTTP {status}: {data}")
        raise YooKassaError(f"Создание платежа отклонено (HTTP {status})")
    return data


async def get_payment(payment_id: str) -> dict:
    """Возвращает актуальный объект платежа по его id."""
    status, data = await _request("GET", f"{_API_URL}/{payment_id}")
    if status != 200:
        logger.error(f"ЮKassa get_payment HTTP {status}: {data}")
        raise YooKassaError(f"Не удалось получить платёж (HTTP {status})")
    return data

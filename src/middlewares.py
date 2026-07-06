"""Middleware сквозного логирования действий пользователей (обязательно по правилам проекта).

Логирует каждое входящее сообщение, команду и нажатие inline-кнопки: дата/время
(через Loguru), username, user_id, first_name, текст. Параллельно пишет действие в
журнал user_events (repo.add_event) — задел под аналитику пути пользователя (этап 9).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from . import repo, texts
from .config import settings
from .logger import logger


def describe_callback(data: str | None) -> str:
    if not data:
        return "Кнопка"
    return f"Кнопка: {data}"


def describe_message(msg: Message) -> str:
    text = msg.text
    if not text:
        return "Прислал медиа"
    if text.startswith("/"):
        return f"Команда {text.split()[0]}"
    short = text if len(text) <= 40 else text[:40] + "…"
    return f"Написал: {short}"


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update: Update = event  # type: ignore[assignment]

        user = None
        label: str | None = None
        if update.message is not None:
            self._log_message(update.message)
            user = update.message.from_user
            label = describe_message(update.message)
        elif update.callback_query is not None:
            self._log_callback(update.callback_query)
            user = update.callback_query.from_user
            label = describe_callback(update.callback_query.data)

        pool = data.get("pool")
        if pool is not None and user is not None and label is not None:
            try:
                await repo.add_event(pool, user.id, label)
            except Exception as e:  # noqa: BLE001 — журнал не должен ронять обработку
                logger.warning(f"Не удалось записать событие истории: {e!r}")

        return await handler(event, data)

    @staticmethod
    def _log_message(msg: Message) -> None:
        u = msg.from_user
        if u is None:
            return
        text = msg.text or "(медиа)"
        logger.info(f"👤 @{u.username or '—'} (id:{u.id}, {u.first_name}) → {text}")

    @staticmethod
    def _log_callback(cb: CallbackQuery) -> None:
        u = cb.from_user
        logger.info(
            f"👤 @{u.username or '—'} (id:{u.id}, {u.first_name}) → [кнопка] {cb.data}"
        )


class AntiAbuseMiddleware(BaseMiddleware):
    """Защита от абьюза (этап 8): лимит длины сообщения + rate-limit флуда.

    Применяется только к входящим сообщениям (inline-кнопки ИИ не стоят и не жгут
    токены — их не режем, чтобы не ломать оплату/навигацию). Команды (`/start`,
    `/admin`, `/id`) пропускаем всегда — это запасной выход из любого залипания.
    Rate-limit — на Redis-счётчике с TTL-окном; предупреждаем один раз за окно,
    дальше молча гасим, чтобы не спамить в ответ на спам.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update: Update = event  # type: ignore[assignment]
        msg = update.message
        # Не сообщение (callback/прочее) — не наше дело, пропускаем дальше.
        if msg is None or msg.from_user is None:
            return await handler(event, data)

        text = msg.text or ""
        # Команды — всегда пропускаем (escape-hatch), их не абьюзят по токенам.
        if text.startswith("/"):
            return await handler(event, data)

        redis = data.get("redis")
        uid = msg.from_user.id

        # 1) Rate-limit флуда (Redis INCR + EXPIRE окна).
        if redis is not None and await self._is_flooding(redis, uid, msg):
            return  # поток остановлен, handler не вызываем

        # 2) Лимит длины (после rate-limit: флуд коротышами тоже гасится).
        if len(text) > settings.max_message_length:
            await msg.answer(texts.too_long(settings.max_message_length))
            logger.info(
                f"⛔ Слишком длинное сообщение @{msg.from_user.username or '—'} "
                f"(id:{uid}, {len(text)} симв.) — не отправлено в модель"
            )
            return

        return await handler(event, data)

    @staticmethod
    async def _is_flooding(redis: Any, uid: int, msg: Message) -> bool:
        """True = лимит превышен (сообщение гасим). Предупреждаем один раз за окно."""
        key = f"urist:rl:{uid}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, settings.rate_limit_window_seconds)
        except Exception as e:  # noqa: BLE001 — сбой Redis не должен ронять обработку
            logger.warning(f"Rate-limit: сбой Redis ({e!r}) — пропускаю без лимита")
            return False

        if count <= settings.rate_limit_messages:
            return False
        # Первое превышение в окне — предупреждаем; дальше молча гасим.
        if count == settings.rate_limit_messages + 1:
            await msg.answer(texts.RATE_LIMITED)
            logger.info(
                f"⛔ Rate-limit @{msg.from_user.username or '—'} (id:{uid}): "
                f"{count} сообщ. за {settings.rate_limit_window_seconds}с — гашу"
            )
        return True


# Кнопки навигации/управления и команды — лёгкие действия и запасные выходы. Их
# single-flight НЕ блокирует, иначе при зависшем/долгом ответе юзер не сможет
# отменить/выйти/переключить экран (тупик).
_PASS_LABELS = frozenset(
    {
        texts.BTN_ASK,
        texts.BTN_EMPLOYER,
        texts.BTN_BALANCE,
        texts.BTN_HELP,
        texts.BTN_MAIN_MENU,
        texts.BTN_NEW_DIALOG,
        texts.BTN_ANSWER_NOW,
        texts.BTN_CANCEL,
        texts.BTN_CHECK_ANOTHER,
    }
)


class BusyMiddleware(BaseMiddleware):
    """Single-flight на пользователя: пока идёт тяжёлый ИИ-поток (сбор → поиск →
    ответ), новые содержательные сообщения того же юзера НЕ запускают параллельную
    обработку. Иначе (юзер строчит несколько вопросов подряд быстрее, чем бот
    отвечает) получаем гонку FSM/памяти в Redis, кратные списания баланса и потерю
    ответов (ответы затирают друг друга). Занятому отвечаем «подождите» — запрос не
    списывается, поток не запускается.

    Замок — Redis SET NX EX; TTL страхует от зависшего потока (тогда замок протухнет
    сам, юзер не залипнет). Пропускаем всегда: callback'и, команды и кнопки навигации
    /управления — это лёгкие действия и запасные выходы, блокировать их нельзя.
    Регистрируется ПОСЛЕ анти-абьюза (флуд/длину режем раньше, до взятия замка).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update: Update = event  # type: ignore[assignment]
        msg = update.message
        if msg is None or msg.from_user is None:
            return await handler(event, data)

        text = msg.text or ""
        # Команды и кнопки навигации — всегда пропускаем (escape-hatch, не тяжёлые).
        if not text or text.startswith("/") or text in _PASS_LABELS:
            return await handler(event, data)

        redis = data.get("redis")
        if redis is None:  # без Redis замок не поставить — не ломаем обработку
            return await handler(event, data)

        uid = msg.from_user.id
        key = f"urist:busy:{uid}"
        try:
            acquired = await redis.set(
                key, "1", nx=True, ex=settings.busy_lock_ttl_seconds
            )
        except Exception as e:  # noqa: BLE001 — сбой Redis не должен ронять обработку
            logger.warning(f"Busy-lock: сбой Redis ({e!r}) — пропускаю без замка")
            return await handler(event, data)

        if not acquired:
            await msg.answer(texts.BUSY)
            logger.info(
                f"⏳ Занят @{msg.from_user.username or '—'} (id:{uid}): сообщение "
                f"отклонено — предыдущий вопрос ещё в обработке (без списания)"
            )
            return

        try:
            return await handler(event, data)
        finally:
            try:
                await redis.delete(key)
            except Exception as e:  # noqa: BLE001 — не снятый замок протухнет по TTL
                logger.warning(f"Busy-lock: не снял замок (id:{uid}): {e!r}")

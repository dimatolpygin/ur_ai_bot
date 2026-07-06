"""Конфигурация проекта. Все значения берутся из переменных окружения (.env)."""
from __future__ import annotations

import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Telegram ─────────────────────────────────────────────────────────────
    bot_token: str
    admin_ids: str = ""

    # ── База данных ──────────────────────────────────────────────────────────
    # DSN для asyncpg (runtime). Пример: postgresql://user:pass@host:5432/db
    database_url: str
    # Отдельная схема под этот бот — чужие таблицы на ПК НЕ трогаем.
    db_schema: str = "urist_bot"

    # ── Redis (FSM + кеш + память диалога) ───────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── ИИ: OpenRouter (этап 2+) ─────────────────────────────────────────────
    openrouter_api_key: str = ""
    # Служебный слой (FSM/решения/уточнения) и финальный ответ + веб-поиск.
    model_service: str = "google/gemini-2.5-flash-lite"
    model_answer: str = "google/gemini-2.5-flash"
    # Базовый URL OpenRouter (OpenAI-совместимый chat/completions).
    ai_base_url: str = "https://openrouter.ai/api/v1"
    ai_request_timeout: int = 60  # секунд на один вызов модели
    ai_max_tokens: int = 1200  # кап токенов ответа — защита баланса владельца
    ai_temperature: float = 0.3

    # ── Память диалога (Redis, этап 2) ───────────────────────────────────────
    # Сколько последних сообщений (user+assistant) держим в контексте.
    dialog_memory_messages: int = 10
    dialog_ttl_seconds: int = 259200  # 3 дня — чистим брошенные диалоги

    # ── Сбор ситуации / COLLECTING (этап 4) ──────────────────────────────────
    # Служебная модель (flash-lite) добирает детали расплывчатого вопроса.
    max_collect_steps: int = 3  # кап уточняющих вопросов (§8: 2–3)
    collect_confidence: float = 0.75  # ранний выход, когда картина ясна (§8)
    collect_max_tokens: int = 1000  # кап токенов на служебное JSON-решение
    # (запас: полная схема на кириллице + quick_replies ≈ до 700 токенов; обрезка
    #  по лимиту ломает JSON, поэтому держим потолок выше пикового ответа)
    collect_retries: int = 3  # ретраи служебного вызова: провайдер OpenRouter
    # ~20% отдаёт finish_reason=error с пустым/битым JSON — ретраим, иначе сбор
    # молча деградирует в поиск и уточнение не задаётся

    # ── Веб-поиск / агент (этап 3) ───────────────────────────────────────────
    max_search_steps: int = 4  # кап итераций tool-calling петли (§8 спеки)
    search_results_per_query: int = 5  # сколько результатов берём с провайдера
    search_token_budget: int = 12000  # токен-бюджет на вопрос → форс-финал
    search_timeout: int = 20  # секунд на один вызов поисковика

    # ── Веб-поиск (этап 3+): Tavily основной, Exa/Firecrawl запасные ──────────
    tavily_api_key: str = ""
    exa_api_key: str = ""
    firecrawl_api_key: str = ""

    # ── ЮKassa (этап 6) ──────────────────────────────────────────────────────
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    # Куда вернётся пользователь после оплаты (домена нет → ссылка на бота).
    yookassa_return_url: str = "https://t.me/URIST2026_1_BOT"
    # Прокси для RU-выхода (прод за границей). Пусто — напрямую; http(s)://… или
    # socks5://… — через прокси (socks требует пакет aiohttp_socks).
    yookassa_proxy: str = ""
    # Email для чека 54-ФЗ, пока не собираем реальный у пользователя.
    receipt_email_placeholder: str = "receipt@example.com"
    # Интервал фонового поллера pending-платежей (минуты) — вебхука нет.
    payment_poll_interval_min: int = 1

    # ── Анти-абьюз (этап 8) ──────────────────────────────────────────────────
    # Максимальная длина входящего текста (символов). Длиннее — просим короче,
    # в модель не отправляем (защита от вставки «простыни» и слива токенов).
    max_message_length: int = 1000
    # Rate-limit флуда: не больше N сообщений за окно (секунд) на пользователя.
    # Команды (/start, /admin, /id) под лимит НЕ попадают — это запасной выход.
    rate_limit_messages: int = 5
    rate_limit_window_seconds: int = 10
    # Брошенный диалог сбора: если пользователь вернулся позже этого срока —
    # старый контекст COLLECTING не продолжаем, начинаем сбор заново (не тащим и
    # не оплачиваем устаревшую ситуацию, экономим токены служебной модели).
    collect_dialog_ttl_seconds: int = 900  # 15 минут
    # Single-flight: пока идёт обработка сообщения юзера (сбор → поиск → ответ),
    # его следующие сообщения не запускают параллельный поток. TTL страхует замок
    # от зависшего потока (максимум на обработку одного сообщения с поиском).
    busy_lock_ttl_seconds: int = 180

    # ── Экономика ────────────────────────────────────────────────────────────
    # Сколько бесплатных запросов начисляется новому пользователю на старте.
    free_requests_on_start: int = 3

    # ── Прочее ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @field_validator("db_schema")
    @classmethod
    def _validate_schema(cls, v: str) -> str:
        # Защита от инъекции: имя схемы попадает в DDL/search_path напрямую.
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", v):
            raise ValueError(f"Недопустимое имя схемы: {v}")
        return v

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.replace(" ", "").split(",") if x]

    @property
    def sqlalchemy_url(self) -> str:
        """URL для SQLAlchemy/Alembic — тот же Postgres, но через драйвер asyncpg."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()

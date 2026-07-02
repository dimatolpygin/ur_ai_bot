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

    # ── Веб-поиск (этап 3+): Tavily основной, Exa/Firecrawl запасные ──────────
    tavily_api_key: str = ""
    exa_api_key: str = ""
    firecrawl_api_key: str = ""

    # ── ЮKassa (этап 6) ──────────────────────────────────────────────────────
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""

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

#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Установщик Юр-бота «URIST2026» на чистой Ubuntu.
# Поднимает самодостаточный стек: бот + свой Postgres + Redis в одном docker-compose.
# Данные — в именованных томах, автоперезапуск. Схема БД — urist_bot (чужое не трогаем).
# Использование:  bash scripts/install.sh   (или: curl … | bash после клонирования)
# ─────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
prompt()  { read -rp "$(echo -e "${YELLOW}>>> $1: ${NC}")" "$2"; }
promptp() { read -rsp "$(echo -e "${YELLOW}>>> $1: ${NC}")" "$2"; echo; }

echo ""
echo "=========================================================="
echo "   Юр-бот «URIST2026» — установщик для Ubuntu"
echo "=========================================================="
echo ""

# ── Сбор переменных ──────────────────────────────────────────────────────────
prompt  "Git репозиторий (https://github.com/...)" GIT_REPO
GIT_REPO=${GIT_REPO:-https://github.com/dimatolpygin/ur_ai_bot.git}

prompt  "Ветка для деплоя [master]" BRANCH
BRANCH=${BRANCH:-master}

prompt  "Каталог установки [/opt/urist_bot]" INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-/opt/urist_bot}

echo ""
promptp "BOT_TOKEN (от @BotFather)" BOT_TOKEN
prompt  "ADMIN_IDS (Telegram id админов через запятую)" ADMIN_IDS

echo ""
echo "ИИ — OpenRouter (обязательно):"
promptp "OPENROUTER_API_KEY" OPENROUTER_API_KEY

echo ""
echo "Веб-поиск — можно задать сейчас или позже в /admin (Enter — пропустить)."
echo "Минимум один провайдер нужен для ответов с источниками; основной — Tavily."
promptp "TAVILY_API_KEY" TAVILY_API_KEY
promptp "EXA_API_KEY" EXA_API_KEY
promptp "FIRECRAWL_API_KEY" FIRECRAWL_API_KEY

echo ""
echo "ЮKassa (оплата пакетов). Для боевого приёма платежей — боевые ключи магазина."
prompt  "YOOKASSA_SHOP_ID" YOOKASSA_SHOP_ID
promptp "YOOKASSA_SECRET_KEY" YOOKASSA_SECRET_KEY
prompt  "YOOKASSA_RETURN_URL [https://t.me/URIST2026_1_BOT]" YOOKASSA_RETURN_URL
YOOKASSA_RETURN_URL=${YOOKASSA_RETURN_URL:-https://t.me/URIST2026_1_BOT}
echo ""
warn "Сервер за границей (не РФ). ЮKassa требует выход с российского IP —"
warn "иначе платежи отклоняются. Укажите прокси-выход в РФ (http:// или socks5://)."
prompt  "YOOKASSA_PROXY (Enter — напрямую, без прокси)" YOOKASSA_PROXY

echo ""
prompt  "RECEIPT_EMAIL_PLACEHOLDER (email для чека 54-ФЗ) [receipt@example.com]" RECEIPT_EMAIL_PLACEHOLDER
RECEIPT_EMAIL_PLACEHOLDER=${RECEIPT_EMAIL_PLACEHOLDER:-receipt@example.com}

prompt  "FREE_REQUESTS_ON_START (бесплатных запросов новичку) [3]" FREE_REQUESTS_ON_START
FREE_REQUESTS_ON_START=${FREE_REQUESTS_ON_START:-3}

# Пароль для встроенного Postgres генерируется автоматически.
POSTGRES_PASSWORD=$(openssl rand -hex 24)

echo ""
info "Начинаю установку..."

# ── Docker ───────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  info "Устанавливаю Docker..."
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version &>/dev/null; then
  error "Не найден плагин 'docker compose'. Установите docker-compose-plugin и повторите."
fi
info "Docker: $(docker --version)"

# ── git ──────────────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  info "Устанавливаю git..."
  apt-get update -qq && apt-get install -y -qq git
fi

# ── Клонирование / обновление ────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
  warn "Каталог уже существует — обновляю..."
  cd "$INSTALL_DIR"
  git fetch --all
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  info "Клонирую репозиторий ($BRANCH)..."
  git clone --branch "$BRANCH" "$GIT_REPO" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

# ── .env ─────────────────────────────────────────────────────────────────────
# Если .env уже есть — сохраняем прежний пароль БД, чтобы не потерять данные.
if [[ -f .env ]] && grep -q '^POSTGRES_PASSWORD=' .env; then
  POSTGRES_PASSWORD=$(grep '^POSTGRES_PASSWORD=' .env | head -1 | cut -d= -f2-)
  warn "Использую существующий POSTGRES_PASSWORD из .env"
fi

info "Создаю .env (боевые значения)..."
cat > .env <<ENVEOF
# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}

# ── База данных (встроенный Postgres; DATABASE_URL задаёт compose) ────────────
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DB_SCHEMA=urist_bot

# ── ИИ: OpenRouter ────────────────────────────────────────────────────────────
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
MODEL_SERVICE=google/gemini-2.5-flash-lite
MODEL_ANSWER=google/gemini-2.5-flash

# ── Веб-поиск (можно менять горячо из /admin → app_settings) ──────────────────
TAVILY_API_KEY=${TAVILY_API_KEY}
EXA_API_KEY=${EXA_API_KEY}
FIRECRAWL_API_KEY=${FIRECRAWL_API_KEY}

# ── ЮKassa ────────────────────────────────────────────────────────────────────
YOOKASSA_SHOP_ID=${YOOKASSA_SHOP_ID}
YOOKASSA_SECRET_KEY=${YOOKASSA_SECRET_KEY}
YOOKASSA_RETURN_URL=${YOOKASSA_RETURN_URL}
YOOKASSA_PROXY=${YOOKASSA_PROXY}
RECEIPT_EMAIL_PLACEHOLDER=${RECEIPT_EMAIL_PLACEHOLDER}
PAYMENT_POLL_INTERVAL_MIN=1

# ── Экономика ─────────────────────────────────────────────────────────────────
FREE_REQUESTS_ON_START=${FREE_REQUESTS_ON_START}

# ── Прочее ────────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
ENVEOF
chmod 600 .env

# ── Запуск ───────────────────────────────────────────────────────────────────
info "Собираю и запускаю стек (бот + Postgres + Redis)..."
docker compose up -d --build

echo ""
echo "=========================================================="
info "Установка завершена!"
echo ""
echo "  Каталог:   ${INSTALL_DIR}"
echo "  Логи:      docker compose -f ${INSTALL_DIR}/docker-compose.yml logs -f bot"
echo "  Рестарт:   docker compose -f ${INSTALL_DIR}/docker-compose.yml restart"
echo "  Стоп:      docker compose -f ${INSTALL_DIR}/docker-compose.yml down"
echo ""
echo "  Проверьте бота: отправьте /start → @URIST2026_1_BOT"
echo "  Управление ценами и ключами поиска: команда /admin в боте (id из ADMIN_IDS)."
echo "=========================================================="

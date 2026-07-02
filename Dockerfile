FROM python:3.12-slim

# Логи сразу в stdout, без .pyc.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

# Сначала зависимости — для кеширования слоя.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код и миграции.
COPY . .

CMD ["python", "-m", "src.main"]

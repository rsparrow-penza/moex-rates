# Dockerfile бэкенда moex-rates
#
# ВАЖНЫЙ НЮАНС именно этого проекта: внутри процесса крутится APScheduler.
# Если запустить Flask в несколько воркеров gunicorn, каждый воркер поднимет
# СВОЙ планировщик и будет долбить ММВБ в один час. Поэтому --workers 1:
# фоновая задача — одна, а HTTP-нагрузка у этого сервисика копеечная.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app.py .

# База по умолчанию лежит в /data (том примонтирует compose)
ENV DATABASE_FILE=/data/rates.db

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "app:app"]

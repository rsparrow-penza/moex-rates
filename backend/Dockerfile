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

COPY app.py econ.py .

# База по умолчанию лежит в /data (том примонтирует compose)
ENV DATABASE_FILE=/data/rates.db

# gunicorn не в TTY, поэтому print() из планировщика оседают в буфере и
# «docker logs» молчит ровно тогда, когда мы ищем в логах 403 монитора МСиБ,
# пропущенный снимок борта или потерянный день ЦБ. Строки проекта — единственный
# способ увидеть, что фоновые задачи реально сделали.
ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "app:app"]

# Моex-rates бэкенд: курс USD и CNY с Московской биржи
#
# Что делает:
#   1. ⏰ Раз в час (APScheduler) опрашивает живой API ММВБ и сохраняет курсы в SQLite
#   2. При первом запуске подтягивает историю за последние ~90 дней (backfill)
#   3. Отдаёт данные фронтов: GET /api/rates?from=…&to=…&currencies=USD,CNY
#
# Запуск:  python3 app.py   (по умолчанию http://localhost:5002)

import os
import sqlite3
from datetime import date, datetime, timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_FILE = os.environ.get("DATABASE_FILE", "rates.db")

# Два «источника» с разных сегментов биржи. URL-адреса присланы самим ISS —
# это публичный API, ключ не нужен.
SOURCES = {
    "USD": {
        "title": "Доллар США (фиксинг МОЭК)",
        # живой эндпоинт: CURRENTVALUE обновляется раз в день около 13:00 МСК
        "live": "https://iss.moex.com/iss/engines/currency/markets/index/securities/USDFIXME.json",
        "live_value_key": "CURRENTVALUE",   # поле с курсом в живой выдаче
        "history": "https://iss.moex.com/iss/history/engines/currency/markets/index/securities/USDFIXME.json",
        "history_value_key": "CLOSE",       # поле с курсом в истории (для backfill)
    },
    "CNY": {
        "title": "Китайский юань (спот-торги МОЭК)",
        # основной «народный» инструмент: юань/рубль, секция CETS, торги
        # 10:00–23:50 МСК — цена обновляется весь день
        "live": "https://iss.moex.com/iss/engines/currency/markets/selt/securities/CNYRUB_TOM.json",
        "live_value_key": "LAST",
        "history": "https://iss.moex.com/iss/history/engines/currency/markets/selt/securities/CNYRUB_TOM.json",
        "history_value_key": "WAPRICE",
    },
}

HTTP_TIMEOUT = 20  # секунд: биржа иногда думает долго, вечно ждать нельзя


# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Таблица rates: одна строка = один курс одной валюты за одну дату.
    PRIMARY KEY (currency, rate_date) — «двойной замок»: повторная запись
    за тот же день не создаст дубль (INSERT OR REPLACE обновит строку).
    Для юаня можно было бы писать и почасово, но дневной ряд честнее
    сравнивать с долларом, у которого данных раз в сутки.
    """
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rates (
                currency   TEXT NOT NULL,  -- 'USD' | 'CNY'
                rate_date  TEXT NOT NULL,  -- '2026-11-02'
                rate       REAL NOT NULL,  -- 80.56 ₽
                fetched_at TEXT NOT NULL,  -- когда обновили
                PRIMARY KEY (currency, rate_date)
            )
            """
        )


init_db()


# ---------------------------------------------------------------------------
# Работа с API ММВБ (внешний мир — здесь возможны сбои!)
# ---------------------------------------------------------------------------

def http_get_json(url, params=None):
    """Один GET-запрос к ISS. Ошибку бросаем наверх — решит вызывающий."""
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                        headers={"User-Agent": "moex-rates/1.0 (educational project)"})
    resp.raise_for_status()
    return resp.json()


def upsert_rate(db, currency: str, rate_date: str, rate) -> bool:
    """Пишет/обновляет один курс. Возвращает True, если что-то реально записали."""
    if rate is None:
        return False
    db.execute(
        "INSERT OR REPLACE INTO rates (currency, rate_date, rate, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        (currency, rate_date, float(rate), datetime.now().isoformat(timespec="seconds")),
    )
    return True


def fetch_live(currency: str) -> bool:
    """
    Опрос живого эндпоинта: текущее значение курса.
    Возвращает True, если сумели получить и записать число.
    """
    source = SOURCES[currency]
    try:
        payload = http_get_json(source["live"])
        rows = payload["marketdata"]["data"]
        cols = payload["marketdata"]["columns"]
        if not rows:
            print(f"[{currency}] живой эндпоинт вернул пустоту (нет торгов?)")
            return False
        row = dict(zip(cols, rows[0]))
        value = row.get(source["live_value_key"]) or row.get("WAPRICE")
        # Дату берём из отчёта биржи (TRADEDATE), а не из часов сервера:
        # ночью биржа отдаёт вчерашнюю сделку — и это правильно.
        rate_date = row.get("TRADEDATE") or date.today().isoformat()
        if value is None:
            return False
        with get_db() as db:
            return upsert_rate(db, currency, rate_date, value)
    except Exception as e:                       # сеть, JSON, структура поплыли…
        print(f"[{currency}] ошибка живого опроса: {e}")
        return False


def backfill(currency: str, days: int = 90) -> int:
    """
    Подтягивает историю за days дней (первый запуск, чтобы таблица
    не была пустой). Нюанс ММВБ: история одного инструмента умещается
    на одну страницу ответа (100 строк), а пагинация параметром skip
    у этого эндпоинта работает странно. Поэтому идём «окнами» по 14 дней:
    14 дат × 3 секции = ~42 строки — заведомо одна страница.
    """
    source = SOURCES[currency]
    saved = 0
    start = date.today() - timedelta(days=days)
    try:
        while start <= date.today():
            stop = min(start + timedelta(days=13), date.today())
            payload = http_get_json(source["history"], params={
                "from": start.isoformat(),
                "to": stop.isoformat(),
            })
            hist = payload.get("history", {})
            cols = hist.get("columns", [])
            with get_db() as db:
                for raw in hist.get("data", []):
                    row = dict(zip(cols, raw))
                    value = row.get(source["history_value_key"]) or row.get("WAPRICE")
                    if value:
                        upsert_rate(db, currency, row["TRADEDATE"], value)
                        saved += 1
            start = stop + timedelta(days=1)
        print(f"[{currency}] backfill: записано {saved} дневных курсов")
        return saved
    except Exception as e:
        print(f"[{currency}] ошибка backfill: {e}")
        return saved


# ---------------------------------------------------------------------------
# Планировщик: «раз в час»
# ---------------------------------------------------------------------------

def hourly_job():
    """Вызывается APScheduler каждый час."""
    for currency in SOURCES:
        fetch_live(currency)


# Переменные окружения управляются не зря:
#   SKIP_SCHEDULER=1 — не поднимать фоновый планировщик (нужно тестам)
#   SKIP_SEED=1      — не лезть в сеть при старте (то же самое для тестов)
#
# Хитрый WERKZEUG_RUN_MAIN: в debug-режиме Flask запускает файл ДВАЖДЫ
# (родитель-«наблюдатель» и рабочий потомок). Если бы мы стартовали
# планировщик безусловно, курс приходил бы дважды в час, а seed долбал
# бы биржу при каждом сохранении файла.
# «Настоящий» процесс — это либо:
#   • потомок dev-сервера (Flask выставляет ему WERKZEUG_RUN_MAIN=true), либо
#   • импорт модуля боевым сервером (gunicorn делает import app — и
#     __name__ уже не "__main__", а "app").
_is_main_process = (
    os.environ.get("WERKZEUG_RUN_MAIN") == "true"   # рабочий потомок dev-сервера
    or __name__ != "__main__"                       # импорт из gunicorn
)

if _is_main_process and not os.environ.get("SKIP_SCHEDULER"):
    scheduler = BackgroundScheduler()
    scheduler.add_job(hourly_job, "interval", hours=1, id="fetch_moex")
    scheduler.start()

if _is_main_process and not os.environ.get("SKIP_SEED"):
    def seed_on_startup():
        """Первый запуск: история + свежий снимок, не дожидаясь часа."""
        for currency in SOURCES:
            backfill(currency)
            fetch_live(currency)
    seed_on_startup()


# ---------------------------------------------------------------------------
# API для фронтенда
# ---------------------------------------------------------------------------

@app.route("/api/currencies", methods=["GET"])
def currencies():
    """Справочник валют — фронт строит из него чекбоксы."""
    return jsonify([{"code": c, "title": s["title"]} for c, s in SOURCES.items()])


@app.route("/api/rates", methods=["GET"])
def rates():
    """
    GET /api/rates?from=2026-10-01&to=2026-11-02&currencies=USD,CNY
    from/to необязательные; currencies — через запятую, по умолчанию все.
    """
    from_ = request.args.get("from")
    to_ = request.args.get("to")
    # currencies=USD,CNY — что прислали, то и фильтруем; пусто/неизвестно — все валюты
    raw_selected = [c.strip().upper() for c in request.args.get("currencies", "").split(",")]
    selected = [c for c in raw_selected if c in SOURCES] or list(SOURCES.keys())

    where, params = [], []
    if from_:
        where.append("rate_date >= ?"); params.append(from_)
    if to_:
        where.append("rate_date <= ?"); params.append(to_)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    placeholders = ",".join("?" * len(selected))
    sql = (f"SELECT currency, rate_date, rate, fetched_at FROM rates "
           f"{where_sql} AND currency IN ({placeholders}) " if where else
           f"SELECT currency, rate_date, rate, fetched_at FROM rates "
           f"WHERE currency IN ({placeholders}) ")
    params = params + selected

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/health", methods=["GET"])
def health():
    """Для дашбордов и себя: жив ли сервис и что в базе."""
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM rates").fetchone()["n"]
        last = db.execute("SELECT MAX(fetched_at) AS t FROM rates").fetchone()["t"]
    return jsonify({"ok": True, "rows": count, "last_update": last})


if __name__ == "__main__":
    app.run(port=5002, debug=True)

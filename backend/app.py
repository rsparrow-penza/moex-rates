# Moex-rates бэкенд: курсы валют с ММВБ + внебиржевой USD (Yahoo)
#
# Что делает:
#   1. ⏰ Раз в час (APScheduler) опрашивает источники и сохраняет курсы в SQLite
#      (для Yahoo-OTC — ещё и внутридневные точки за последние сутки)
#   2. При первом запуске подтягивает историю за последние ~90 дней (backfill)
#   3. Отдаёт данные фронту: GET /api/rates?from=…&to=…&currencies=…
#
# Запуск:  python3 app.py   (по умолчанию http://localhost:5002)

import os
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_FILE = os.environ.get("DATABASE_FILE", "rates.db")
MSK = ZoneInfo("Europe/Moscow")   # живём в биржевом времени, а не в UTC сервера

# Источники данных. У каждого есть provider: «moex» (публичный ISS-API биржи)
# или «yahoo» (неофициальный, но общедоступный endpoint Yahoo Finance,
# котировки OTC-межбанка — обновляются круглосуточно, каждые ~15 минут).
SOURCES = {
    "USD": {
        "provider": "moex",
        "title": "Доллар США (фиксинг МОЭК)",
        # живой эндпоинт: CURRENTVALUE обновляется раз в день около 13:00 МСК
        "live": "https://iss.moex.com/iss/engines/currency/markets/index/securities/USDFIXME.json",
        "live_value_key": "CURRENTVALUE",   # поле с курсом в живой выдаче
        "history": "https://iss.moex.com/iss/history/engines/currency/markets/index/securities/USDFIXME.json",
        "history_value_key": "CLOSE",       # поле с курсом в истории (для backfill)
    },
    "USD_OTC": {
        "provider": "yahoo",
        "title": "Доллар, межбанк OTC (Yahoo)",
        # тикер Yahoo для пары USD/RUB; interval/range — «последние сутки, шаг 15 мин»
        "live": "https://query1.finance.yahoo.com/v8/finance/chart/RUB=X",
        "ticker": "RUB=X",
    },
    "EUR_OTC": {
        "provider": "yahoo",
        "title": "Евро, межбанк OTC (Yahoo)",
        # та же площадка, что в приложении «Акции» на iPhone (тикер EURRUB=X)
        "live": "https://query1.finance.yahoo.com/v8/finance/chart/EURRUB=X",
        "ticker": "EURRUB=X",
    },
    "EUR": {
        "provider": "moex",
        "title": "Евро (фиксинг МОЭК)",
        # как и доллар: спот-торги евро приостановлены, биржа публикует фиксинг
        "live": "https://iss.moex.com/iss/engines/currency/markets/index/securities/EURFIXME.json",
        "live_value_key": "CURRENTVALUE",
        "history": "https://iss.moex.com/iss/history/engines/currency/markets/index/securities/EURFIXME.json",
        "history_value_key": "CLOSE",
    },
    "CNY": {
        "provider": "moex",
        "title": "Китайский юань (спот-торги МОЭК)",
        # основной «народный» инструмент: юань/рубль, секция CETS, торги
        # 10:00–23:50 МСК — цена обновляется весь день
        "live": "https://iss.moex.com/iss/engines/currency/markets/selt/securities/CNYRUB_TOM.json",
        "live_value_key": "LAST",
        "history": "https://iss.moex.com/iss/history/engines/currency/markets/selt/securities/CNYRUB_TOM.json",
        "history_value_key": "WAPRICE",
    },
    # ── Официальные курсы ЦБ РФ ────────────────────────────────────────────
    # Один XML ЦБ содержит все валюты разом, поэтому три кода-валюты
    # обслуживает ОДНА функция (EUR_CBR/CNY_CBR — «aliased» на USD_CBR:
    # их данные приходят тем же запросом, дублировать HTTP незачем).
    "USD_CBR": {
        "provider": "cbr",
        "title": "Доллар, официальный курс ЦБ",
        "cbr_charcode": "USD",
    },
    "EUR_CBR": {
        "provider": "cbr",
        "title": "Евро, официальный курс ЦБ",
        "cbr_charcode": "EUR",
        "alias_of": "USD_CBR",     # его данные грузятся вместе с USD_CBR
    },
    "CNY_CBR": {
        "provider": "cbr",
        "title": "Юань, официальный курс ЦБ",
        "cbr_charcode": "CNY",
        "alias_of": "USD_CBR",
    },
}

# Единая точка входа официального API ЦБ (не «зеркало», а сам cbr.ru).
# date_req в формате дд.мм.гггг возвращает курс, ДЕЙСТВУЮЩИЙ в этот день, —
# работает и для выходных (курс выходного дня = пятничный, как и в жизни).
CBR_XML_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

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
    Таблица rates: одна строка = один курс (валюта + источник + момент).
    PRIMARY KEY (currency, source, rate_date, rate_time) — «четверной замок»:
    повторная запись того же момента не создаёт дубль, а обновляет строку.

    Дневные значения ММВБ хранят в rate_time пустую строку, внутридневные
    точки Yahoo — 'HH:MM' по Москве. Так USD-фиксинг и USD-OTC живут в одной
    таблице, не мешая друг другу, и их можно рисовать двумя линиями.
    """
    with get_db() as db:
        # Есть ли уже таблица? Нужна ли миграция (старая схема без source)?
        cols = [r["name"] for r in db.execute("PRAGMA table_info(rates)")]
        if cols and "source" not in cols:
            # Миграция со старой схемы (currency, rate_date, rate, fetched_at).
            # ВАЖНО: нельзя «перенести через CREATE TABLE AS SELECT» — SQLite
            # при этом НЕ копирует PRIMARY KEY (это мы на своей шкуре и
            # поймали). Поэтому: читаем старые строки в память, пересоздаём
            # таблицу по новой схеме и пишем прочитанное обратно как MOEX-дневные.
            old = db.execute("SELECT currency, rate_date, rate, fetched_at FROM rates").fetchall()
            db.execute("DROP TABLE rates")
            db.execute(
                """
                CREATE TABLE rates (
                    currency   TEXT NOT NULL,
                    source     TEXT NOT NULL,
                    rate_date  TEXT NOT NULL,
                    rate_time  TEXT NOT NULL,
                    rate       REAL NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (currency, source, rate_date, rate_time)
                )
                """
            )
            for r in old:
                db.execute(
                    "INSERT OR REPLACE INTO rates VALUES (?,?,?,?,?,?)",
                    (r["currency"], "MOEX", r["rate_date"], "", r["rate"], r["fetched_at"]),
                )
            print(f"миграция: перенесено {len(old)} строк в новую схему")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS rates (
                currency   TEXT NOT NULL,  -- 'USD' | 'USD_OTC' | 'EUR' | 'CNY'
                source     TEXT NOT NULL,  -- 'MOEX' | 'YAHOO'
                rate_date  TEXT NOT NULL,  -- '2026-11-02'
                rate_time  TEXT NOT NULL,  -- 'HH:MM' МСК либо '' для дневных
                rate       REAL NOT NULL,  -- 80.56 ₽
                fetched_at TEXT NOT NULL,  -- когда обновили
                PRIMARY KEY (currency, source, rate_date, rate_time)
            )
            """
        )


init_db()


# ---------------------------------------------------------------------------
# Работа с внешними API (здесь возможны сбои — и они нормальны)
# ---------------------------------------------------------------------------

def http_get_json(url, params=None):
    """Один GET-запрос с разбором JSON. Ошибку бросаем наверх — решит вызывающий."""
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                        headers={"User-Agent": "moex-rates/1.0 (educational project)"})
    resp.raise_for_status()
    return resp.json()


def http_get_xml_text(url, params=None):
    """
    Тот же GET, но для ЦБ: их XML отдаётся в кодировке windows-1251
    (да, в 2026-м так бывает). requests угадает cp1251 по заголовку,
    но перестраховаться — явно указать надёжнее.
    """
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                        headers={"User-Agent": "moex-rates/1.0 (educational project)"})
    resp.raise_for_status()
    return resp.content.decode("windows-1251")


def upsert_rate(db, currency, source, rate_date, rate_time, rate) -> bool:
    """Пишет/обновляет один курс. Возвращает True, если что-то реально записали."""
    if rate is None:
        return False
    db.execute(
        "INSERT OR REPLACE INTO rates (currency, source, rate_date, rate_time, rate, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (currency, source, rate_date, rate_time, float(rate),
         datetime.now().isoformat(timespec="seconds")),
    )
    return True


def fetch_live_moex(currency: str) -> bool:
    """
    Опрос живого эндпоинта ММВБ: текущее (дневное) значение курса.
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
        # Отвечает несколько строк (по торговым секциям); берём первую с ценой.
        # Дату берём из отчёта биржи (TRADEDATE), а не из часов сервера:
        # ночью биржа отдаёт вчерашнюю сделку — и это правильно.
        for raw in rows:
            row = dict(zip(cols, raw))
            value = row.get(source["live_value_key"]) or row.get("WAPRICE")
            if value is not None:
                rate_date = row.get("TRADEDATE") or date.today().isoformat()
                with get_db() as db:
                    return upsert_rate(db, currency, "MOEX", rate_date, "", value)
        return False
    except Exception as e:                       # сеть, JSON, структура поплыли…
        print(f"[{currency}] ошибка живого опроса: {e}")
        return False


def fetch_live_yahoo(currency: str) -> bool:
    """
    Опрос Yahoo Finance: OTC-котировка USD/RUB.
    Заодно вытягиваем весь внутридневной ряд за сутки (interval=15m, range=1d) —
    Yahoo отдаёт его одним ответом. Благодаря этому даже после простоя
    (сервер спал выходные) на графике восстанавливается целостная змея,
    а не одна точка «когда проснулись».
    """
    source = SOURCES[currency]
    try:
        payload = http_get_json(source["live"], params={
            "interval": "15m", "range": "1d",
        })
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])
        saved = 0
        with get_db() as db:
            for ts, price in zip(stamps, closes):
                if price is None:
                    continue
                # Yahoo отдаёт unix-time в UTC — переводим в Москву и там
                # делим на «момент + время», чтобы сутки не рвались на 23:59
                msk = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MSK)
                if upsert_rate(db, currency, "YAHOO",
                               msk.date().isoformat(), msk.strftime("%H:%M"), price):
                    saved += 1
        print(f"[{currency}] yahoo: записано {saved} внутридневных точек")
        return saved > 0
    except Exception as e:
        print(f"[{currency}] ошибка yahoo-опроса: {e}")
        return False


def fetch_cbr_day(day: date) -> int:
    """
    Официальный курс ЦБ на дату `day` (day может быть выходным — ЦБ вернёт
    актуальный на этот день «замороженный» курс). Один запрос закрывает сразу
    все три валюты. Возвращает количество записанных значений.

    Формат даты у ЦБ — «дд.мм.гггг» (да, с точками; ещё один привет из 90-х).
    Курсы в XML разделены запятой («80,5573») — превращаем в точку.
    Nominal: для USD/EUR/CNY он равен 1, но на будущее делим Value на Nominal,
    иначе курс юаня «на 10 юаней» улетел бы в 10 раз.
    """
    try:
        raw = http_get_xml_text(CBR_XML_URL, params={"date_req": day.strftime("%d.%m.%Y")})
        root = ET.fromstring(raw)
        wanted = {s["cbr_charcode"]: code for code, s in SOURCES.items()
                  if s["provider"] == "cbr"}
        saved = 0
        with get_db() as db:
            for valute in root.iter("Valute"):
                charcode = valute.findtext("CharCode")
                if charcode not in wanted:
                    continue
                rate = float(valute.findtext("VunitRate").replace(",", "."))
                # день, за который просили, и есть «действующий» день курса
                upsert_rate(db, wanted[charcode], "CBR", day.isoformat(), "", rate)
                saved += 1
        return saved
    except Exception as e:
        print(f"[CBR] ошибка опроса {day}: {e}")
        return 0


def fetch_live(currency: str) -> bool:
    """Диспетчер: провайдер валюты решает, кто его опрашивает."""
    provider = SOURCES[currency]["provider"]
    if provider == "yahoo":
        return fetch_live_yahoo(currency)
    if provider == "cbr":
        # один запрос ЦБ отдаёт все валюты: неважно, какой код дёрнули
        today_msk = datetime.now(MSK).date()
        return fetch_cbr_day(today_msk) > 0
    return fetch_live_moex(currency)


def backfill(currency: str, days: int = 90) -> int:
    """
    Подтягивает дневную историю за days дней (первый запуск, чтобы таблица
    не была пустой).

    • ММВБ: история одного инструмента умещается на одну страницу ответа
      (100 строк), а пагинация параметром skip у этого эндпоинта работает
      странно. Поэтому идём «окнами» по 14 дней.
    • ЦБ: по одному запросу на день (дата-параметр есть, а пакетной истории
      нет) — зато один запрос закрывает все три валюты, поэтому «алиасы»
      EUR_CBR/CNY_CBR ничего не качают: их данные приносит USD_CBR.
    • Yahoo: «история» — последние сутки, их берёт fetch_live_yahoo.
    """
    source = SOURCES[currency]
    if source["provider"] == "yahoo":
        return 0
    if source["provider"] == "cbr":
        if "alias_of" in source:
            return 0                       # грузится вместе с USD_CBR
        saved = 0
        for i in range(days):              # день за днём, от древних к сегодняшнему
            saved += fetch_cbr_day(date.today() - timedelta(days=i))
        print(f"[{currency}] backfill ЦБ: записано {saved} значений (все валюты)")
        return saved
    # ── moex ──
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
                        upsert_rate(db, currency, "MOEX", row["TRADEDATE"], "", value)
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
    """
    Вызывается APScheduler каждый час. Группирует «aliased» валюты:
    ЦБ отвечает одним XML на все валюты, поэтому три запроса в час к нему —
    просто навязчивость, делаем один.
    """
    done_groups = set()
    for currency, source in SOURCES.items():
        group = source.get("alias_of", currency)
        if group in done_groups:
            continue
        done_groups.add(group)
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
        hourly_job()
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
    GET /api/rates?from=2026-10-01&to=2026-11-02&currencies=USD,USD_OTC,CNY
    from/to необязательные; currencies — через запятую, по умолчанию все.
    Ответ сортирован по (дата, время) — фронт рисует линии в этом порядке.
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
    sql = (f"SELECT currency, source, rate_date, rate_time, rate, fetched_at FROM rates "
           f"{where_sql} AND currency IN ({placeholders}) " if where else
           f"SELECT currency, source, rate_date, rate_time, rate, fetched_at FROM rates "
           f"WHERE currency IN ({placeholders}) ")
    params = params + selected
    sql += "ORDER BY currency, rate_date, rate_time"

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

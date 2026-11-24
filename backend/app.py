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
import re
import sqlite3
import time
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

# Источники данных. У каждого есть provider: «moex» (публичный ISS-API биржи),
# «yahoo» (неофициальный, но общедоступный endpoint Yahoo Finance — OTC-
# межбанк обновляется круглосуточно, каждые ~15 минут) или «coingecko»
# (криптобиржи: bitcoin 24/7, курс к рублю).
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
    # ── Не валюты, а активы: bitcoin и нефть — для графика курсов ──────────
    # В таблице дневных ₽ их нет (источник не MOEX/CBR), только линии.
    "BTC": {
        "provider": "coingecko",
        "title": "Bitcoin (CoinGecko, ₽)",
        "live": "https://api.coingecko.com/api/v3/simple/price",
        "history": "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
    },
    "BRENT": {
        "provider": "yahoo",
        "title": "Нефть Brent (ICE, $ за баррель)",
        # тот же chart-endpoint, что у OTC, только тикер фьючерса Brent;
        # backfill_daily — в отличие от валют, историю качаем дневную (90 дней)
        "live": "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F",
        "ticker": "BZ=F",
        "backfill_daily": True,
    },
    "GOLD": {
        "provider": "yahoo",
        "title": "Золото (COMEX, $ за тройскую унцию)",
        # ISS-металлы ММВБ снаружи недоступны, берём мировую цену: GC=F
        "live": "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        "ticker": "GC=F",
        "backfill_daily": True,
    },
}

# Единая точка входа официального API ЦБ (не «зеркало», а сам cbr.ru).
# date_req в формате дд.мм.гггг возвращает курс, ДЕЙСТВУЮЩИЙ в этот день, —
# работает и для выходных (курс выходного дня = пятничный, как и в жизни).
CBR_XML_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

# Ключевая ставка: пакетного XML/JSON у ЦБ нет, зато есть страница с полной
# таблицей «дата → ставка» (каждый календарный день, данные с 2013 года).
# Глубина истории фиксированная — с начала 2020 года: окно не «едет», и
# календарь заседаний на фронте (cbrMeetings.js) покрывает её целиком.
KEY_RATE_URL = "https://www.cbr.ru/hd_base/KeyRate/"
KEY_RATE_SINCE = "2020-01-01"   # самый первый день хранящейся истории ставки

# RUONIA — на своей странице ЦБ, но без deep-истории: берём что отдаёт
# (последние дни) и накапливаем сами.
RUONIA_URL = "https://www.cbr.ru/hd_base/ruonia/"

# Топ-10 банков: живого открытого источника (указатель/реестр ЦБ) в сети
# нет — рейтинг хранится в таблице top_banks и обновляется POST-ом с
# секретом из TOP_BANKS_SECRET. Семя — актуальный срез на дату AS_OF.
TOP_BANKS_AS_OF = "01.09.2026"
TOP_BANKS_SEED = [
    # name, assets(трлн ₽), n1.0(%), profit(млрд ₽), siber
    ("Сбербанк",          63.9, 13.5, 1730, 1),
    ("ВТБ",               33.4, 10.9,  540, 1),
    ("Газпромбанк",       18.6, 12.4,  380, 1),
    ("Россельхозбанк",     8.9, 14.2,   46, 1),
    ("Альфа-Банк",         8.3, 11.8,  470, 1),
    ("Промсвязьбанк",      8.0, 11.9,   62, 1),
    ("Совкомбанк",         6.8, 14.0,   40, 1),
    ("Т-Банк",             5.9, 16.4,  122, 0),
    ("Московский кредитный банк", 5.1, 13.6, 34, 0),
    ("Озон Банк",          3.0, 18.1,   25, 0),
]

# Официальная годовая инфляция (ИПЦ, % к предыдущему году) по России —
# открытый JSON Всемирного банка (агрегатор Росстата): без ключей,
# пересчитывается раз в год. Глубина — сколько отдаёт источник (с 1990-х).
INFLATION_URL = "https://api.worldbank.org/v2/country/RUS/indicator/FP.CPI.TOTL.ZG"

HTTP_TIMEOUT = 20  # секунд: биржа иногда думает долго, вечно ждать нельзя

# ММВБ на некоторых хостах (VPS в том числе) отдаёт кривую цепочку TLS —
# сертификат выписан «Russian Trusted Sub CA», которого нет в стандартной
# корневой хранилище, и handshake падает. Для iss.moex.com в этом случае
# идём по http: это тот же публичный read-only ISS, без учётных данных.
MOEX_HTTP_HOSTS = ("iss.moex.com",)


def moex_url(url: str) -> str:
    """HTTPS-адрес ММВБ → HTTP, если за этим хостом TLS не проходит."""
    if os.environ.get("MOEX_FORCE_HTTP") == "1":
        for host in MOEX_HTTP_HOSTS:
            if host in url:
                return url.replace("https://", "http://", 1)
    return url


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

        # Ключевая ставка ЦБ: отдельная таблица — данные другого вида
        # (проценты, не рубли) и другой живой. Одна строка = один календарный
        # день; ставка «действует каждый день», поэтому храним полный дневной
        # ряд, а не только даты изменений — график тогда рисуется без дырок.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS key_rate (
                rate_date  TEXT PRIMARY KEY,  -- '2026-11-13'
                rate       REAL NOT NULL,     -- 14.00 (годовых, %)
                fetched_at TEXT NOT NULL
            )
            """
        )

        # RUONIA: средняя ставка межбанковских кредитов в рублях. ЦБ отдаёт
        # на странице только последнее число, поэтому ряд «откормится» сам:
        # каждый часовой обход добавляет день-два, история копится у нас.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ruonia (
                rate_date  TEXT PRIMARY KEY,
                rate       REAL NOT NULL,     -- % годовых
                fetched_at TEXT NOT NULL
            )
            """
        )

        # Годовая инфляция: один ряд, один год = одна строка.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS inflation (
                year       INTEGER PRIMARY KEY,  -- 1993..2025
                rate       REAL NOT NULL,        -- % к предыдущему году
                fetched_at TEXT NOT NULL
            )
            """
        )

        # Топ-10 банков: рейтинг без живого источника — хранится у нас и
        # обновляется вручную (POST /api/top-banks с токеном). Пустая таблица
        # при первом запуске наполняется семенным срезом.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS top_banks (
                name    TEXT PRIMARY KEY,
                assets  REAL NOT NULL,   -- трлн ₽
                n1      REAL NOT NULL,   -- норматив достаточности капитала, %
                profit  REAL NOT NULL,   -- чистая прибыль за год, млрд ₽
                siber   INTEGER NOT NULL,-- 1 — системно значимый
                rank    INTEGER NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS top_banks_meta (
                id     INTEGER PRIMARY KEY CHECK (id = 1),
                as_of  TEXT NOT NULL,    -- дата отчётности среза
                updated_at TEXT NOT NULL
            )
            """
        )
        if not db.execute("SELECT 1 FROM top_banks LIMIT 1").fetchone():
            for i, (name, assets, n1, profit, siber) in enumerate(TOP_BANKS_SEED, 1):
                db.execute(
                    "INSERT INTO top_banks (name, assets, n1, profit, siber, rank) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, assets, n1, profit, siber, i))
            db.execute(
                "INSERT INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, ?)",
                (TOP_BANKS_AS_OF, datetime.now().isoformat(timespec="seconds")))


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


def http_get_xml_text(url, params=None, encoding="windows-1251"):
    """
    Тот же GET, но для ЦБ: их XML отдаётся в кодировке windows-1251
    (да, в 2026-м так бывает). requests угадает cp1251 по заголовку,
    но перестраховаться — явно указать надёжнее.
    HTML-страницы ЦБ, наоборот, в UTF-8 — для них кодировку передают
    параметром (см. fetch_key_rate).
    """
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT,
                        headers={"User-Agent": "moex-rates/1.0 (educational project)"})
    resp.raise_for_status()
    return resp.content.decode(encoding)


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
        payload = http_get_json(moex_url(source["live"]))
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


def fetch_live_coingecko(currency: str) -> bool:
    """
    Bitcoin: CoinGecko simple/price — текущая цена в рублях. Крипторынок
    работает 24/7, поэтому точку пишем со временем: на графике биткоин —
    непрерывная «змея», как OTC-валюты.
    """
    source = SOURCES[currency]
    try:
        payload = http_get_json(source["live"],
                                params={"ids": "bitcoin", "vs_currencies": "rub"})
        price = payload["bitcoin"]["rub"]
        now = datetime.now(MSK)
        with get_db() as db:
            ok = upsert_rate(db, currency, "COINGECKO",
                             now.date().isoformat(), now.strftime("%H:%M"), price)
        print(f"[{currency}] coingecko: {price} ₽")
        return ok
    except Exception as e:
        print(f"[{currency}] ошибка coingecko-опроса: {e}")
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


def fetch_key_rate() -> int:
    """
    Ключевая ставка ЦБ: скачиваем страницу с полной историей и разбираем
    таблицу «день → ставка». ЦБ отдаёт каждый календарный день (ставка
    действует круглосуточно, включая выходные), формат строк:
        <td>13.11.2026</td> <td>14,00</td>
    Записываем всё с даты KEY_RATE_SINCE (начало 2020 года), более раннее
    чистим — глубина истории постоянна и полностью покрыта календарём
    заседаний на фронте. Возвращает число записанных строк.
    """
    cutoff = KEY_RATE_SINCE
    try:
        # dateFrom/dateTo ЦБ понимает прямо в URL (дд.мм.гггг) — одним запросом
        # получаем ровно нужное окно, а не всю историю с 2013 года.
        day = datetime.now(MSK).date()
        html = http_get_xml_text(KEY_RATE_URL, encoding="utf-8", params={
            "dateFrom": datetime.strptime(KEY_RATE_SINCE, "%Y-%m-%d").strftime("%d.%m.%Y"),
            "dateTo": day.strftime("%d.%m.%Y"),
        })
        rows = re.findall(
            r"<td>(\d{2})\.(\d{2})\.(\d{4})</td>\s*<td>([\d]+,\d+)</td>", html)
        saved = 0
        fetched_at = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            for dd, mm, yyyy, value in rows:
                iso = f"{yyyy}-{mm}-{dd}"
                if iso < cutoff:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO key_rate (rate_date, rate, fetched_at) VALUES (?, ?, ?)",
                    (iso, float(value.replace(",", ".")), fetched_at))
                saved += 1
            removed = db.execute("DELETE FROM key_rate WHERE rate_date < ?", (cutoff,)).rowcount
        if removed:
            print(f"[key_rate] почистил {removed} строк старше {cutoff}")
        print(f"[key_rate] записано {saved} дневных значений")
        return saved
    except Exception as e:
        print(f"[key_rate] ошибка опроса: {e}")
        return 0


def fetch_live(currency: str) -> bool:
    """Диспетчер: провайдер валюты решает, кто его опрашивает."""
    provider = SOURCES[currency]["provider"]
    if provider == "yahoo":
        return fetch_live_yahoo(currency)
    if provider == "coingecko":
        return fetch_live_coingecko(currency)
    if provider == "cbr":
        # один запрос ЦБ отдаёт все валюты: неважно, какой код дёрнули
        today_msk = datetime.now(MSK).date()
        return fetch_cbr_day(today_msk) > 0
    return fetch_live_moex(currency)


def backfill_yahoo_daily(currency: str, days: int) -> int:
    """
    Дневная история для «товарных» yahoo-тикеров (Brent): тот же chart-endpoint,
    но interval=1d. Валютным OTC это не нужно — у них хватает суток 15-минуток.
    """
    source = SOURCES[currency]
    try:
        payload = http_get_json(source["live"], params={"interval": "1d", "range": f"{days}d"})
        result = payload["chart"]["result"][0]
        stamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])
        saved = 0
        with get_db() as db:
            for ts, price in zip(stamps, closes):
                if price is None:
                    continue
                msk = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MSK)
                # дневное закрытие: без времени — точка «в день», как у биржи
                if upsert_rate(db, currency, "YAHOO", msk.date().isoformat(), "", price):
                    saved += 1
        print(f"[{currency}] yahoo backfill: записано {saved} дневных закрытий")
        return saved
    except Exception as e:
        print(f"[{currency}] ошибка yahoo-backfill: {e}")
        return 0


def backfill_coingecko(currency: str, days: int) -> int:
    """
    Дневная история биткоина: market_chart отдаёт prices = [[ms, цена], ...]
    одним ответом на любой запрос — идеальная пакетная история.
    """
    try:
        payload = http_get_json(SOURCES[currency]["history"],
                                params={"vs_currency": "rub", "days": days,
                                        "interval": "daily"})
        saved = 0
        with get_db() as db:
            for ms, price in payload.get("prices", []):
                if price is None:
                    continue
                msk = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(MSK)
                if upsert_rate(db, currency, "COINGECKO", msk.date().isoformat(), "", price):
                    saved += 1
        print(f"[{currency}] coingecko backfill: записано {saved} дневных цен")
        return saved
    except Exception as e:
        print(f"[{currency}] ошибка coingecko-backfill: {e}")
        return 0


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
    • Yahoo: «история» — последние сутки, их берёт fetch_live_yahoo. Исключение
      — товарные тикеры с флагом backfill_daily (Brent): у них просим 1d-ряд.
    • CoinGecko: market_chart сразу отдаёт дневную историю за N дней.
    """
    source = SOURCES[currency]
    if source["provider"] == "yahoo":
        if source.get("backfill_daily"):
            return backfill_yahoo_daily(currency, days)
        return 0
    if source["provider"] == "coingecko":
        return backfill_coingecko(currency, days)
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
            payload = http_get_json(moex_url(source["history"]), params={
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


def fetch_ruonia() -> int:
    """
    RUONIA: страница ЦБ отдаёт таблицу «даты → ставка» за последние дни.
    Даты — первой строкой, значения — в строке «Ставка RUONIA, % годовых».
    Deep-истории у источника нет, поэтому каждый обход дописывает новые дни
    к уже накопленным (INSERT OR REPLACE — дубли безопасны).
    """
    try:
        html = http_get_xml_text(RUONIA_URL, encoding="utf-8")
        dates = re.findall(r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>", html)
        m = re.search(r"Ставка RUONIA[^%]*%\s*годовых\s*</td>(.*?)</tr>", html, re.S)
        if not m or not dates:
            print("[ruonia] таблица не распознана")
            return 0
        values = re.findall(r"<td>\s*([\d]+,\d+)\s*</td>", m.group(1))
        saved = 0
        fetched_at = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            for d, v in zip(dates, values):
                dd, mm, yyyy = d.split(".")
                db.execute(
                    "INSERT OR REPLACE INTO ruonia (rate_date, rate, fetched_at) VALUES (?, ?, ?)",
                    (f"{yyyy}-{mm}-{dd}", float(v.replace(",", ".")), fetched_at))
                saved += 1
        print(f"[ruonia] записано {saved} значений")
        return saved
    except Exception as e:
        print(f"[ruonia] ошибка опроса: {e}")
        return 0


def fetch_inflation() -> int:
    """
    Годовая инфляция (ИПЦ, % к предыдущему году): открытый JSON Всемирного
    банка по России. Формат ответа — [метаданные, [строки]], в строках
    date=год, value=None для лет, которых ещё нет в базе. Пересчитывается
    раз в год — часовой обход просто убеждается, что число свежее.
    """
    try:
        payload = http_get_json(INFLATION_URL, params={
            "format": "json",
            "date": f"1990:{datetime.now(MSK).year}",
            "per_page": 200,
        })
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        saved = 0
        fetched_at = datetime.now().isoformat(timespec="seconds")
        with get_db() as db:
            for r in rows:
                if r.get("value") is None:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO inflation (year, rate, fetched_at) VALUES (?, ?, ?)",
                    (int(r["date"]), float(r["value"]), fetched_at))
                saved += 1
        print(f"[inflation] записано {saved} годов")
        return saved
    except Exception as e:
        print(f"[inflation] ошибка опроса: {e}")
        return 0


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
    # Ставку тоже освежаем этим же часовым обходом: страница ЦБ одна,
    # а изменение ставки — событие редкое, часовая задержка незаметна.
    fetch_key_rate()
    fetch_ruonia()
    fetch_inflation()


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
    def _has_backfill(currency: str) -> bool:
        """Должен ли этот код что-то выкачивать при backfill.
        Yahoo-OTC валюты — нет (история = сутки из fetch_live), алиасы ЦБ — нет."""
        s = SOURCES[currency]
        if s["provider"] == "yahoo":
            return bool(s.get("backfill_daily"))
        if s["provider"] == "cbr":
            return "alias_of" not in s
        return True

    def seed_on_startup():
        """Первый запуск: история + свежий снимок, не дожидаясь часа.
        Пустой backfill у «источника с историей» — скорее всего временный
        лимит (Yahoo/CoinGecko иногда молчат после шквала запросов), поэтому
        такие коды получают одну повторную попытку после короткой паузы."""
        empty = [c for c in SOURCES if not backfill(c) and _has_backfill(c)]
        if empty:
            print(f"[seed] пусто по {empty} — повторяю через 5 секунд")
            time.sleep(5)
            for currency in empty:
                backfill(currency)
        hourly_job()     # он же подтянет и ключевую ставку
    seed_on_startup()


# ---------------------------------------------------------------------------
# API для фронтенда
# ---------------------------------------------------------------------------

@app.route("/api/currencies", methods=["GET"])
def currencies():
    """Справочник валют — фронт строит из него чекбоксы.
    provider нужен фронту, чтобы отличать дневные ₽-ряды (таблица) от
    внутридневных «змей» и активов (график без таблицы)."""
    return jsonify([{"code": c, "title": s["title"], "provider": s["provider"]}
                    for c, s in SOURCES.items()])


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


@app.route("/api/key-rate", methods=["GET"])
def key_rate():
    """
    GET /api/key-rate — дневной ряд ключевой ставки ЦБ с KEY_RATE_SINCE
    (начало 2020 года). from/to необязательные, как у /api/rates. Ответ по
    возрастанию даты: фронт по нему рисует ступенчатый график и находит
    дату изменений.
    """
    from_ = request.args.get("from")
    to_ = request.args.get("to")
    where, params = [], []
    if from_:
        where.append("rate_date >= ?"); params.append(from_)
    if to_:
        where.append("rate_date <= ?"); params.append(to_)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT rate_date, rate FROM key_rate {where_sql} "
           "ORDER BY rate_date")
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/ruonia", methods=["GET"])
def ruonia():
    """
    GET /api/ruonia — накопленный нами дневной ряд ставки RUONIA.
    from/to необязательные. Ряд растёт с каждого часового обхода:
    источник отдаёт только последние дни, история — наша собственная.
    """
    from_ = request.args.get("from")
    to_ = request.args.get("to")
    where, params = [], []
    if from_:
        where.append("rate_date >= ?"); params.append(from_)
    if to_:
        where.append("rate_date <= ?"); params.append(to_)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT rate_date, rate FROM ruonia {where_sql} ORDER BY rate_date"
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/inflation", methods=["GET"])
def inflation():
    """
    GET /api/inflation — годовая инфляция (ИПЦ, % к предыдущему году),
    сколько есть в базе (с 1990-х). from/to — по году, необязательные.
    """
    from_ = request.args.get("from")
    to_ = request.args.get("to")
    where, params = [], []
    if from_:
        where.append("year >= ?"); params.append(int(from_))
    if to_:
        where.append("year <= ?"); params.append(int(to_))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT year, rate FROM inflation {where_sql} ORDER BY year"
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/top-banks", methods=["GET"])
def top_banks():
    """
    GET /api/top-banks — текущий срез рейтинга топ-10 банков + дата отчётности.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT rank, name, assets, n1, profit, siber FROM top_banks ORDER BY rank").fetchall()
        meta = db.execute("SELECT as_of, updated_at FROM top_banks_meta WHERE id = 1").fetchone()
    return jsonify({"as_of": meta["as_of"] if meta else "",
                    "updated_at": meta["updated_at"] if meta else "",
                    "banks": [dict(r) for r in rows]})


@app.route("/api/top-banks", methods=["POST"])
def top_banks_update():
    """
    POST /api/top-banks — обновить срез: {"as_of": "01.12.2026",
    "banks": [{"name": ..., "assets": ..., "n1": ..., "profit": ...,
               "siber": true}, ...]} в порядке убывания активов.

    Доступ по заголовку X-Admin-Token (секрет в переменной окружения
    TOP_BANKS_SECRET): без секретного слова — 503, с неверным — 401.
    Тело проверяем на минимальную вменяемость: 3..25 банков, обязательные
    поля, числа положительнее нуля — чтобы опечаткой не снести блок.
    """
    secret = os.environ.get("TOP_BANKS_SECRET")
    if not secret:
        return jsonify({"ok": False, "error": "обновление отключено: задайте TOP_BANKS_SECRET"}), 503
    if request.headers.get("X-Admin-Token") != secret:
        return jsonify({"ok": False, "error": "неверный токен"}), 401

    data = request.get_json(silent=True) or {}
    as_of = str(data.get("as_of", "")).strip()
    banks = data.get("banks")
    if not as_of or not isinstance(banks, list) or not (3 <= len(banks) <= 25):
        return jsonify({"ok": False, "error": "нужны as_of и список banks из 3..25 элементов"}), 400

    rows = []
    for i, b in enumerate(banks, 1):
        try:
            rows.append((b["name"], float(b["assets"]), float(b["n1"]),
                         float(b["profit"]), 1 if b.get("siber") else 0, i))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": f"строка {i}: нужны name, assets, n1, profit (числа)"}), 400

    with get_db() as db:
        db.execute("DELETE FROM top_banks")
        db.executemany(
            "INSERT INTO top_banks (name, assets, n1, profit, siber, rank) VALUES (?, ?, ?, ?, ?, ?)",
            rows)
        db.execute(
            """
            INSERT INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET as_of = excluded.as_of, updated_at = excluded.updated_at
            """,
            (as_of, datetime.now().isoformat(timespec="seconds")))
    return jsonify({"ok": True, "count": len(rows), "as_of": as_of})


@app.route("/api/health", methods=["GET"])
def health():
    """Для дашбордов и себя: жив ли сервис и что в базе."""
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM rates").fetchone()["n"]
        last = db.execute("SELECT MAX(fetched_at) AS t FROM rates").fetchone()["t"]
    return jsonify({"ok": True, "rows": count, "last_update": last})


if __name__ == "__main__":
    app.run(port=5002, debug=True)

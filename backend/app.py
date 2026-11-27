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

# курсовая история живёт отдельно — econ.py подключается после init_db(),
# ему нужен рабочий get_db
import econ

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
        "title": "Bitcoin (CoinGecko, $)",
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
#
# Все цифры — отчётность одного банка по РСБУ, которую ЦБ выкладывает в
# разделе «Информация по кредитным организациям → Формы отчётности»
# (зеркало pervyschet.ru, сверено с рейтингом Банки.ру):
#   assets   — активы нетто, форма 0409101
#   cap123   — собственные средства (капитал), строка 000 формы 0409123
#   n1       — норматив Н1.0, форма 0409135 (0 = нет данных)
#   profit   — чистая прибыль с начала года (счёт 706 формы 0409101)
#   vkl_fiz  — вклады физлиц: сумма 42.2 (счета 423/426) + 522
#   sred_org — средства юрлиц: 401–407 + 410–422/42.1/427–439/43.1 + 521
# None = банк форму не публикует (у ПСБ нет 0409101, поэтому нет активов,
# прибыли и клиентских денег) — на фронте прочерк.
TOP_BANKS_AS_OF = "01.09.2026"
TOP_BANKS_SEED = [
    # name, assets, n1.0, profit (млрд ₽), siber, cap123, vkl_fiz, sred_org — всё в трлн ₽
    ("Сбербанк",          68.8, 13.1, 1332.7, 1, 8.1, 19.8, 15.7),
    ("ВТБ",               36.8,  9.7,  378.2, 1, 2.4,  8.6, 13.4),
    ("Газпромбанк",       18.0, 11.5,  124.7, 1, 1.7,  2.5,  8.1),
    ("Альфа-Банк",        13.7, 11.5,  226.3, 1, 1.3,  3.0,  4.4),
    ("Промсвязьбанк",     None, 11.6,   None, 1, 1.0, None, None),  # ф.101 не публикует
    ("Россельхозбанк",     5.4, 13.1,   72.5, 1, 0.6,  1.5,  2.1),
    ("Т-Банк",             5.5, 13.0,  131.8, 0, 0.7,  2.8,  0.7),
    ("Московский кредитный банк", 4.8, 11.4, 17.9, 1, 0.4, 0.7, 1.9),
    ("Совкомбанк",         4.2, 11.4,   86.1, 1, 0.5,  0.9,  1.9),
    ("Озон Банк",          1.0, 21.7,    27.5, 0, 0.1,  0.1,  0.1),
]

# Вторая группа «избранные банки» — не рейтинг, а срез, который смотрим
# сами: Новиком, ББР, Хлынов, Кузнецкий. Место (place) — по активам нетто
# в РФ на 01.09.2026 (указатель ЦБ в терминах отчётности ЦБ).
TOP_BANKS_EXTRA = [
    # name, assets, n1.0, profit (млрд ₽), siber, place, cap123, vkl_fiz, sred_org
    ("Новикомбанк",    1.6, 13.0,   18.1, 0, 13, 0.13, 0.11, 1.16),
    ("ББР",            0.21, 18.2,    1.2, 0, 43, 0.03, 0.04, 0.07),
    ("Хлынов",         0.05, 14.0,    0.8, 0, 94, 0.007, 0.025, 0.010),
    ("Кузнецкий",      0.011, 11.8,  -0.04, 0, 173, 0.0014, 0.002, 0.0),
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
    """
    Одно соединение к базе: планировщик (отдельный поток), стартовый seed и
    обработчики HTTP живут с одним файлом, поэтому:
      • timeout / PRAGMA busy_timeout = 15 с. Дефолт модуля sqlite3 — 5 с
        (у чистой SQLite и вовсе 0), а часовой обход ЦБ пишет сотни строк
        подряд: в логах это кончалось «database is locked» и потерянными
        днями, то есть ожидания хватало не всегда;
      • WAL включаем один раз в init_db() — он хранится в файле базы и
        главное: читатели больше не ждут писателя вообще.
    """
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.execute("PRAGMA busy_timeout = 15000")
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
        # WAL: часовая запись перестала блокировать чтение из API. Если
        # файловая система его не поддерживает (сетевой том), SQLite сама
        # останется в journal_mode=delete — это не ошибка.
        mode = db.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if mode and mode.lower() != "wal":
            print(f"бд: WAL недоступен ({mode}), работаем в штатном режиме")
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
        # при первом запуске наполняется семенным срезом. grp различает
        # рейтинг ('top') и избранную четвёрку ('extra'), place — место по
        # активам в РФ (знаем для extras, для топ-10 это и есть rank).
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS top_banks (
                name      TEXT PRIMARY KEY,
                assets    REAL,               -- трлн ₽ (NULL = банк не публикует форму 0409101)
                n1        REAL NOT NULL,      -- норматив достаточности капитала, % (0 = нет данных)
                profit    REAL,               -- чистая прибыль с начала года, млрд ₽ (NULL = нет данных)
                siber     INTEGER NOT NULL,   -- 1 — системно значимый
                rank      INTEGER NOT NULL,
                grp       TEXT NOT NULL DEFAULT 'top',
                place     INTEGER,            -- место по активам в РФ (для extras)
                cap123    REAL,               -- капитал по форме № 123, трлн ₽ (NULL = нет данных)
                vkl_fiz   REAL,               -- вклады физлиц, трлн ₽
                sred_org  REAL                -- средства предприятий и организаций, трлн ₽
            )
            """
        )
        # Миграция старой схемы (без grp/place, потом без новых показателей):
        # SQLite добавит колонки с значениями по умолчанию.
        cols = [r["name"] for r in db.execute("PRAGMA table_info(top_banks)")]
        if "grp" not in cols:
            db.execute("ALTER TABLE top_banks ADD COLUMN grp TEXT NOT NULL DEFAULT 'top'")
        if "place" not in cols:
            db.execute("ALTER TABLE top_banks ADD COLUMN place INTEGER")
        for col in ("cap123", "vkl_fiz", "sred_org"):
            if col not in cols:
                db.execute(f"ALTER TABLE top_banks ADD COLUMN {col} REAL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS top_banks_meta (
                id     INTEGER PRIMARY KEY CHECK (id = 1),
                as_of  TEXT NOT NULL,    -- дата отчётности среза
                updated_at TEXT NOT NULL
            )
            """
        )
        if not db.execute("SELECT 1 FROM top_banks WHERE grp = 'top' LIMIT 1").fetchone():
            for i, (name, assets, n1, profit, siber, cap123, vkl_fiz, sred_org) in enumerate(TOP_BANKS_SEED, 1):
                db.execute(
                    "INSERT OR REPLACE INTO top_banks (name, assets, n1, profit, siber, rank, grp,"
                    " cap123, vkl_fiz, sred_org) VALUES (?, ?, ?, ?, ?, ?, 'top', ?, ?, ?)",
                    (name, assets, n1, profit, siber, i, cap123, vkl_fiz, sred_org))
            db.execute(
                "INSERT INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, ?)",
                (TOP_BANKS_AS_OF, datetime.now().isoformat(timespec="seconds")))
        if not db.execute("SELECT 1 FROM top_banks WHERE grp = 'extra' LIMIT 1").fetchone():
            for i, (name, assets, n1, profit, siber, place, cap123, vkl_fiz, sred_org) in enumerate(TOP_BANKS_EXTRA, 1):
                db.execute(
                    "INSERT OR REPLACE INTO top_banks (name, assets, n1, profit, siber, rank, grp, place,"
                    " cap123, vkl_fiz, sred_org) VALUES (?, ?, ?, ?, ?, ?, 'extra', ?, ?, ?, ?)",
                    (name, assets, n1, profit, siber, i, place, cap123, vkl_fiz, sred_org))


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


# «Экономика» (econ.py) пользуется нашими же get_db/http_get_json/moex_url —
# связываем сразу после определения фетчера
# обёртки, а не сами функции: тесты подменяют http_get_json в модуле,
# и econ.py должен видеть подмену
econ.configure(lambda: get_db(),
               lambda url, params=None: http_get_json(url, params),
               lambda url: moex_url(url))
econ.init_econ_db()


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
    Bitcoin: CoinGecko simple/price — текущая цена в долларах (мировая котировка,
    рубли здесь только шумят). Крипторынок работает 24/7, поэтому точку пишем
    со временем: на графике биткоин — непрерывная «змея», как OTC-валюты.
    """
    source = SOURCES[currency]
    try:
        payload = http_get_json(source["live"],
                                params={"ids": "bitcoin", "vs_currencies": "usd"})
        price = payload["bitcoin"]["usd"]
        now = datetime.now(MSK)
        with get_db() as db:
            ok = upsert_rate(db, currency, "COINGECKO",
                             now.date().isoformat(), now.strftime("%H:%M"), price)
        print(f"[{currency}] coingecko: {price} $")
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
    одним ответом на любой запрос — идеальная пакетная история. Валюта — доллар.
    """
    try:
        payload = http_get_json(SOURCES[currency]["history"],
                                params={"vs_currency": "usd", "days": days,
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
    econ.register_jobs(scheduler)
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
        # макроряды «Экономики»: econ_seed сам смотрит, чего в базе нет
        econ.econ_seed()
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
    GET /api/top-banks — текущий срез рейтинга топ-10 и избранной четвёрки
    (grp='extra', с place — местом по активам в РФ) + дата отчётности.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT rank, name, assets, n1, profit, siber, grp, place,"
            " cap123, vkl_fiz, sred_org FROM top_banks ORDER BY grp DESC, rank").fetchall()
        meta = db.execute("SELECT as_of, updated_at FROM top_banks_meta WHERE id = 1").fetchone()
    return jsonify({"as_of": meta["as_of"] if meta else "",
                    "updated_at": meta["updated_at"] if meta else "",
                    "banks": [dict(r) for r in rows]})


@app.route("/api/top-banks", methods=["POST"])
def top_banks_update():
    """
    POST /api/top-banks — обновить срез: {"as_of": "01.12.2026",
    "banks": [{"name": ..., "assets": ..., "n1": ..., "profit": ...,
               "siber": true, "grp": "top"|"extra", "place": 13,
               "cap123": 8.1, "vkl_fiz": 19.8, "sred_org": 15.7}, ...]}
    в порядке убывания активов; grp и place необязательные (по умолчанию
    'top' и null). Все деньги — отчётность одного банка по РСБУ (формы
    0409101/0409123/0409135): assets/cap123/vkl_fiz/sred_org — трлн ₽,
    profit — чистая прибыль с начала года, млрд ₽. Любое из денежных
    полей может отсутствовать или быть null (банк не публикует форму) —
    на фронте это прочерк. rank проставляется сам — порядком внутри
    группы.

    Доступ по заголовку X-Admin-Token (секрет в переменной окружения
    TOP_BANKS_SECRET): без секретного слова — 503, с неверным — 401.
    Тело проверяем на минимальную вменяемость: 3..25 банков, обязательные
    поля, активы и капитал числами — чтобы опечаткой не снести блок.
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

    def num_opt(b, key):
        """Необязательное числовое поле: отсутствует или null -> None."""
        v = b.get(key)
        return None if v is None else float(v)

    rows = []
    seen = {"top": 0, "extra": 0}
    for i, b in enumerate(banks, 1):
        grp = b.get("grp", "top")
        if grp not in ("top", "extra"):
            return jsonify({"ok": False, "error": f"строка {i}: grp должен быть top или extra"}), 400
        try:
            seen[grp] += 1
            rows.append((b["name"], num_opt(b, "assets"), float(b["n1"]),
                         num_opt(b, "profit"), 1 if b.get("siber") else 0,
                         seen[grp], grp, b.get("place"),
                         num_opt(b, "cap123"), num_opt(b, "vkl_fiz"), num_opt(b, "sred_org")))
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": f"строка {i}: нужны name, n1 (число), assets/profit — числа или null"}), 400

    with get_db() as db:
        db.execute("DELETE FROM top_banks")
        db.executemany(
            "INSERT INTO top_banks (name, assets, n1, profit, siber, rank, grp, place,"
            " cap123, vkl_fiz, sred_org) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows)
        db.execute(
            """
            INSERT INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET as_of = excluded.as_of, updated_at = excluded.updated_at
            """,
            (as_of, datetime.now().isoformat(timespec="seconds")))
    return jsonify({"ok": True, "count": len(rows), "as_of": as_of})


# ---------------------------------------------------------------------------
# «Экономика»: ставки ЦБ, вклады топ-10, ОФЗ, реальный сектор, бюджет
# ---------------------------------------------------------------------------

@app.route("/api/econ/meta", methods=["GET"])
def econ_meta():
    """Справочник рядов для фронта: ключ, подпись, единицы, группа, источник."""
    out = [{"key": k, **{f: v[f] for f in ("label", "unit", "group", "source", "explain")}}
           for k, v in econ.ECON_SERIES.items()]
    for secid, shortname, matdate in econ.OFZ_UNIVERSE:
        out.append({"key": f"bond_yld_{secid}", "label": shortname, "unit": "%",
                    "group": "bonds", "source": "MOEX",
                    "explain": f"доходность к погашению ОФЗ с датой погашения {matdate}"})
    return jsonify(out)


@app.route("/api/econ/series", methods=["GET"])
def econ_series():
    """
    GET /api/econ/series?key=rate_corp_long&from=2024-01&to=2026-11
    Один ряд из econ_series (месячные/квартальные/дневные — как лежит).
    """
    key = request.args.get("key", "")
    if key not in econ.ECON_SERIES and not key.startswith("bond_yld_"):
        return jsonify({"error": f"неизвестный ряд {key}"}), 404
    sql = "SELECT period, value FROM econ_series WHERE key = ?"
    params = [key]
    if request.args.get("from"):
        sql += " AND period >= ?"; params.append(request.args["from"])
    if request.args.get("to"):
        sql += " AND period <= ?"; params.append(request.args["to"])
    with get_db() as db:
        rows = db.execute(sql + " ORDER BY period", params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/econ/bonds", methods=["GET"])
def econ_bonds():
    """
    Кривая ОФЗ: последний дневной снимок + история доходностей за окно
    (from/to — YYYY-MM-DD, по умолчанию последний год). Фронту хватает
    одного ответа и на «снимок кривой», и на «линии по месяцам».
    """
    till = request.args.get("to") or datetime.now(MSK).date().isoformat()
    since = request.args.get("from") or (
        datetime.now(MSK).date() - timedelta(days=365)).isoformat()
    today = datetime.now(MSK).date().isoformat()
    with get_db() as db:
        snap_date = db.execute(
            "SELECT MAX(snap_date) AS d FROM ofz_snapshot").fetchone()["d"]
        # к снимку присоединяем справочник выпуска: дата эмиссии, купон,
        # номинал, объём — они меняются редко, но фронту нужны в той же строке
        curve = db.execute(
            "SELECT s.secid, s.shortname, s.matdate, s.yield_prc, s.price_prc,"
            " s.price_chg_prc, s.accrued, s.valtoday_rur, s.snap_date,"
            " i.issuedate, i.coupon_prc, i.face_rur, i.issue_size_mlrd"
            " FROM ofz_snapshot s LEFT JOIN ofz_info i USING (secid)"
            " WHERE s.snap_date = ? ORDER BY s.matdate", (snap_date,)).fetchall() if snap_date else []
        curve = [dict(c) for c in curve]
        seen = {c["secid"] for c in curve}
        # planned exit: выпуск ещё торгуется, но его выход из корзины известен
        for c in curve:
            lin = econ.OFZ_LINEAGE.get(c["secid"])
            if not lin:
                continue
            c["exit_date"] = lin["exit"]
            c["replaced_by"] = lin["replaced_by"]
            c["replaced_by_name"] = econ.OFZ_NAMES.get(lin["replaced_by"], lin["replaced_by"])
            # раз выпуск лежит в сегодняшнем снимке — биржа его ещё торгует,
            # и серой строки делать нельзя: дата погашения по календарю это
            # ещё не вывод с рынка (эмиссию делистят через несколько дней).
            # «Погашен» включается только когда строка из сегодняшнего снимка
            # исчезла — ниже, в ветке выведенных
            c["retired"] = False
        # выведенные: биржа их уже не отдаёт, и в сегодняшнем снимке их нет —
        # берём последнюю реальную запись, чтобы строка не исчезала молча
        for secid, lin in econ.OFZ_LINEAGE.items():
            if secid in seen or lin["exit"] >= today:
                continue
            last = db.execute(
                "SELECT s.secid, s.shortname, s.matdate, s.yield_prc, s.price_prc,"
                " s.price_chg_prc, s.accrued, s.valtoday_rur, s.snap_date,"
                " i.issuedate, i.coupon_prc, i.face_rur, i.issue_size_mlrd"
                " FROM ofz_snapshot s LEFT JOIN ofz_info i USING (secid)"
                " WHERE s.secid = ? ORDER BY s.snap_date DESC LIMIT 1", (secid,)).fetchone()
            if not last:
                continue      # вывод случился до того, как мы вели запись — выдумывать нечего
            row = dict(last)
            row.update({"exit_date": lin["exit"], "retired": True,
                        "replaced_by": lin["replaced_by"],
                        "replaced_by_name": econ.OFZ_NAMES.get(lin["replaced_by"], lin["replaced_by"])})
            curve.append(row)
        curve.sort(key=lambda c: c["matdate"])
        series = {}
        for secid, _sn, _md in list(econ.OFZ_UNIVERSE) + [(k, "", "") for k in econ.OFZ_LINEAGE]:
            rows = db.execute(
                "SELECT period, value FROM econ_series WHERE key = ?"
                " AND period >= ? AND period <= ? ORDER BY period",
                (f"bond_yld_{secid}", since, till)).fetchall()
            if rows:
                series[secid] = [{"date": r["period"], "yield": r["value"]} for r in rows]
    return jsonify({"snap_date": snap_date, "curve": [dict(c) for c in curve],
                    "series": series})


@app.route("/api/ofz/all", methods=["GET"])
def ofz_all():
    """
    GET /api/ofz/all — все выпуски ОФЗ с борта TQOB (дневной срез ofz_board).

    Таблица живая: строка = строка биржевого борта, поэтому новые выпуски
    Минфина появляются сами и ничего не надо править в коде. Прошлый снимок
    остаётся, пока не будет нового, — фронт всегда видит корректную дату.

    В ответе есть flags: какие выпуски входят в фиксированную корзину кривой
    (in_basket) и есть ли уже подтянутая история доходности (has_series) —
    по клику фронт дёргает /api/ofz/series только когда истории нет.
    """
    with get_db() as db:
        day = db.execute("SELECT MAX(snap_date) AS d FROM ofz_board").fetchone()["d"]
        rows = db.execute(
            "SELECT * FROM ofz_board WHERE snap_date = ?"
            " ORDER BY matdate, kind, shortname", (day,)).fetchall() if day else []
        have = {r["key"][len("bond_yld_"):]
                for r in db.execute("SELECT DISTINCT key FROM econ_series WHERE key LIKE 'bond_yld_%'")}
    basket = {secid for secid, _n, _m in econ.OFZ_UNIVERSE}
    retired = set(econ.OFZ_LINEAGE)
    out = []
    for r in rows:
        d = dict(r)
        d["in_basket"] = d["secid"] in basket
        d["retired"] = d["secid"] in retired and not d["in_basket"]
        d["has_series"] = d["secid"] in have
        out.append(d)
    kinds = {}
    for d in out:
        k = kinds.setdefault(d["kind"], {"kind": d["kind"], "label": d["kind_label"], "n": 0})
        k["n"] += 1
    return jsonify({"snap_date": day, "rows": out,
                    "kinds": sorted(kinds.values(), key=lambda k: k["kind"]),
                    "notes": {v[0]: v[1] for v in econ.OFZ_KINDS.values()}})


@app.route("/api/ofz/series", methods=["GET"])
def ofz_series():
    """
    GET /api/ofz/series?secid=SU26232RMFS7&days=400
    Дневной ряд YIELDCLOSE по ЛЮБОМУ выпуску с борта — чтобы добавить его на
    график, не дожидаясь, пока он попадёт в корзину.

    Ряд кладётся в ту же econ_series (ключ bond_yld_<secid>) и дальше
    обновляется общим дневным прогоном только если выпуск в корзине; для
    «разовых» выпусков мы добираем его при повторном клике, если данные
    старше трёх дней. SECID проверяем по борту: запрос произвольной строки не
    должен превращаться в прокси к ММВБ.
    """
    secid = (request.args.get("secid") or "").strip().upper()
    try:
        days = min(max(int(request.args.get("days") or 400), 5), 2000)
    except ValueError:
        return jsonify({"error": "days — целое число дней"}), 400
    if not re.fullmatch(r"[A-Z0-9]{6,20}", secid):
        return jsonify({"error": "некорректный secid"}), 400
    with get_db() as db:
        known = db.execute(
            "SELECT 1 FROM ofz_board WHERE secid = ?"
            " UNION SELECT 1 FROM ofz_snapshot WHERE secid = ? LIMIT 1",
            (secid, secid)).fetchone()
        if not known:
            return jsonify({"error": f"выпуск {secid} не найден на борту TQOB"}), 404
        last = db.execute(
            "SELECT MAX(period) AS p, COUNT(*) AS n FROM econ_series WHERE key = ?",
            (f"bond_yld_{secid}",)).fetchone()
    stale = (not last["n"]) or (
        (datetime.now(MSK).date() - datetime.strptime(last["p"], "%Y-%m-%d").date()).days > 3)
    if stale:
        try:
            econ.fetch_ofz_history(days=days, secids=[secid])
        except Exception as e:
            if not last["n"]:
                return jsonify({"error": f"история не подтянута: {e}"}), 502
            app.logger.info("ofz series: %s не обновилась (%s), отдаём кеш", secid, e)
    with get_db() as db:
        head = db.execute("SELECT shortname, matdate FROM ofz_board WHERE secid = ?"
                          " ORDER BY snap_date DESC LIMIT 1", (secid,)).fetchone()
        pts = db.execute(
            "SELECT period, value FROM econ_series WHERE key = ? ORDER BY period",
            (f"bond_yld_{secid}",)).fetchall()
    return jsonify({"secid": secid,
                    "shortname": head["shortname"] if head else secid,
                    "matdate": head["matdate"] if head else None,
                    "updated": last["p"],
                    "points": [{"date": p["period"], "yield": p["value"]} for p in pts]})


@app.route("/api/budget", methods=["GET"])
def budget_get():
    """
    GET /api/budget — федеральный бюджетный срез: строки по месяцам (накоплено
    с начала года) + as_of. Обновляется вручную (минфин режет ботов).
    """
    with get_db() as db:
        rows = db.execute("SELECT period, field, value FROM budget_rows"
                          " ORDER BY period, field").fetchall()
        meta = db.execute("SELECT as_of, updated_at FROM budget_meta WHERE id = 1").fetchone()
    out = {}
    for r in rows:
        out.setdefault(r["period"], {})[r["field"]] = r["value"]
    return jsonify({"as_of": meta["as_of"] if meta else "",
                    "updated_at": meta["updated_at"] if meta else "",
                    "rows": [{"period": p, **v} for p, v in sorted(out.items())]})


@app.route("/api/budget", methods=["POST"])
def budget_update():
    """
    POST /api/budget — заменить срез: {"as_of": "01.11.2026",
    "rows": [{"period": "2026-09", "revenue": 32100, "oil_gas": 7900,
              "non_oil": 24200, "expenditure": 34800, "deficit": -2700,
              "nd_deficit": -9800}, ...]} (млрд ₽, накоплено с начала года;
    deficit — со знаком: минус = дефицит). Требует X-Admin-Token.
    """
    secret = os.environ.get("TOP_BANKS_SECRET")
    if not secret:
        return jsonify({"ok": False, "error": "обновление отключено: задайте TOP_BANKS_SECRET"}), 503
    if request.headers.get("X-Admin-Token") != secret:
        return jsonify({"ok": False, "error": "неверный токен"}), 401
    data = request.get_json(silent=True) or {}
    as_of = str(data.get("as_of", "")).strip()
    rows = data.get("rows")
    if not as_of or not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "нужны as_of и непустой список rows"}), 400
    clean = []
    for i, r in enumerate(rows, 1):
        try:
            period = str(r["period"])
            rec = {"period": period}
            for f in econ.BUDGET_FIELDS:
                if r.get(f) is not None:
                    rec[f] = float(r[f])
        except (KeyError, TypeError, ValueError):
            return jsonify({"ok": False, "error": f"строка {i}: нужны period и поля числами"}), 400
        clean.append(rec)
    with get_db() as db:
        db.execute("DELETE FROM budget_rows")
        for rec in clean:
            period = rec.pop("period")
            for f, v in rec.items():
                db.execute("INSERT OR REPLACE INTO budget_rows (period, field, value) VALUES (?,?,?)",
                           (period, f, v))
        db.execute(
            """
            INSERT INTO budget_meta (id, as_of, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET as_of = excluded.as_of, updated_at = excluded.updated_at
            """, (as_of, datetime.now().isoformat(timespec="seconds")))
    return jsonify({"ok": True, "count": len(clean), "as_of": as_of})


@app.route("/api/econ/real", methods=["POST"])
def econ_real_update():
    """
    POST /api/econ/real — ручное пополнение рядов, которые Росстат отдаёт
    только в переименованных файлах: {"series": {"retail_turnover":
    {"2026-09": 3120.4}, "retail_yoy": {"2026-09": 4.1}}}. Тот же токен.
    """
    secret = os.environ.get("TOP_BANKS_SECRET")
    if not secret:
        return jsonify({"ok": False, "error": "обновление отключено: задайте TOP_BANKS_SECRET"}), 503
    if request.headers.get("X-Admin-Token") != secret:
        return jsonify({"ok": False, "error": "неверный токен"}), 401
    data = request.get_json(silent=True) or {}
    series = data.get("series") or {}
    unknown = [k for k in series if k not in econ.ECON_SERIES]
    if unknown:
        return jsonify({"ok": False, "error": f"неизвестные ряды: {unknown}"}), 400
    rows = []
    for key, per in series.items():
        for p, v in (per or {}).items():
            try:
                rows.append((key, str(p), float(v)))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"{key}: значения должны быть числами"}), 400
    econ.upsert_series(rows)
    return jsonify({"ok": True, "count": len(rows)})


@app.route("/api/metals", methods=["GET"])
def metals():
    """
    GET /api/metals — биржевые цены на драгметаллы: ближайшие контракты
    ФОРТС (MOEX, $ за тройскую унцию) и мировые COMEX/NYMEX (Yahoo).
    Отдаёт ряды по датам (forts/world могут расходиться на один день:
    ФОРТС живёт по московскому дню, COMEX — по американскому), контрактную
    арифметику считает фронт.
    """
    with get_db() as db:
        contracts = db.execute(
            "SELECT metal, secid, shortname, lasttrade, seen_at FROM metal_contract"
            " ORDER BY metal").fetchall()
        series = {}
        for m in econ.METALS:
            dates = set()
            vals = {}
            for kind in ("forts", "world"):
                rows = db.execute(
                    "SELECT period, value FROM econ_series WHERE key = ? ORDER BY period",
                    (f"metal_{m['metal']}_{kind}",)).fetchall()
                for r in rows:
                    dates.add(r["period"])
                    vals.setdefault(r["period"], {})[kind] = r["value"]
            series[m["metal"]] = [{"date": d, **vals[d]} for d in sorted(dates)]
    return jsonify({
        "metals": [{k: v for k, v in m.items()} for m in econ.METALS],
        "contracts": [dict(c) for c in contracts],
        "series": series,
    })


@app.route("/api/prokat", methods=["GET"])
def prokat():
    """
    GET /api/prokat — цены на металлопрокат, двумя рядами:
      • rosstat — официальные средние цены производителей (Росстат), ₽ за тонну,
        по Российской Федерации, месячные, с 2021 г.;
      • dealer  — мониторинг журнала «Металлоснабжение и сбыт»: дилерские цены
        Центрального региона (Москва), ₽ за тонну, шаг ~2 недели. Здесь же
        арматура, которой нет в статистике.
    meta хранит, из какого файла и на какую дату данные, — фронт показывает
    это в подписи, а не делает вид, что цена «сегодняшняя».
    """
    def shape(items):
        out = []
        for p in items:
            rows = db.execute(
                "SELECT period, value FROM econ_series WHERE key = ? ORDER BY period",
                (p["key"],)).fetchall()
            meta = econ.ECON_SERIES[p["key"]]
            out.append({"key": p["key"], "label": meta["label"], "unit": meta["unit"],
                        "explain": meta["explain"], "source": meta["source"],
                        "code": p.get("code"), "assort": p.get("assort"),
                        "series": [{"period": r["period"], "value": r["value"]} for r in rows]})
        return out

    with get_db() as db:
        body = {
            "rosstat": shape(econ.PROKAT_ROSSTAT),
            "dealer": shape(econ.PROKAT_DEALER + [dict(key="dk_index")]),
            "meta": {r["key"]: r["value"]
                     for r in db.execute("SELECT key, value FROM prokat_meta")},
        }
    return jsonify(body)


@app.route("/api/health", methods=["GET"])
def health():
    """Для дашбордов и себя: жив ли сервис и что в базе."""
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM rates").fetchone()["n"]
        last = db.execute("SELECT MAX(fetched_at) AS t FROM rates").fetchone()["t"]
    return jsonify({"ok": True, "rows": count, "last_update": last})


if __name__ == "__main__":
    app.run(port=5002, debug=True)

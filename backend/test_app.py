# Тесты moex-rates: python3 -m unittest discover -v
#
# Особенности этого проекта:
#   1. Данные приходят ИЗ ИНТЕРНЕТА (ММВБ и Yahoo) — тесты не должны
#      зависеть от биржи, поэтому сетевые запросы ПОДМЕШАЕМЯ (mock):
#      вместо настоящего HTTP подставляем заготовленный JSON.
#   2. Два провайдера (moex/yahoo) и внутридневные точки Yahoo —
#      для них отдельные проверки записи времени.

import os
import tempfile
import unittest
from datetime import date, timedelta, timezone, datetime as dt
from unittest import mock

# Временная база и отключение сети при импорте — ДО импорта приложения
# (app.py читает переменные и поднимает планировщик прямо при загрузке)
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_FILE"] = _tmpdb.name
os.environ["SKIP_SCHEDULER"] = "1"
os.environ["SKIP_SEED"] = "1"

import app as rates  # noqa: E402


# --- Заготовки ответов (живой вид JSON, снят руками с реальных API) ---

MOCK_MOEX_LIVE = {
    "marketdata": {
        "columns": ["SECID", "TRADEDATE", "CURRENTVALUE", "UPDATETIME"],
        "data": [["SEC", "2026-11-02", 80.5573, "13:00:00"]],
    }
}

MOCK_MOEX_LIVE_CNY = {
    "marketdata": {
        "columns": ["SECID", "TRADEDATE", "LAST", "WAPRICE"],
        "data": [["CNYRUB_TOM", "2026-11-02", 12.1234, 12.0789]],
    }
}

MOCK_MOEX_HISTORY = {
    "history": {
        "columns": ["TRADEDATE", "SECID", "OPEN", "LOW", "HIGH", "CLOSE"],
        "data": [
            ["2026-10-31", "USDFIXME", 81.0, 81.0, 81.0, 80.99],
            ["2026-11-01", "USDFIXME", 80.8, 80.8, 80.8, 80.77],
        ],
    }
}

def _yahoo_payload():
    """Две внутридневные точки Yahoo: 09:00 и 10:00 МСК 2 ноября."""
    def msk_ts(hh, mm):
        return int(dt(2026, 11, 2, hh, mm, tzinfo=timezone.utc)
                   .replace(tzinfo=timezone(timedelta(hours=3))).timestamp())
    return {
        "chart": {"result": [{
            "timestamp": [msk_ts(9, 0), msk_ts(10, 0)],
            "indicators": {"quote": [{"close": [80.5, 80.9]}]},
        }]}
    }


class TestMigration(unittest.TestCase):
    """Старая схема (без source) должна переехать в новую без потерь."""

    def test_old_rows_become_moex_daily(self):
        import sqlite3
        dbfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        con = sqlite3.connect(dbfile)
        con.execute("CREATE TABLE rates (currency TEXT, rate_date TEXT, rate REAL,"
                    " fetched_at TEXT, PRIMARY KEY (currency, rate_date))")
        con.execute("INSERT INTO rates VALUES ('USD','2026-11-01', 80.0, 'x')")
        con.commit(); con.close()

        with mock.patch.object(rates, "DB_FILE", dbfile):
            rates.init_db()   # повторный вызов = миграция
            with rates.get_db() as db:
                row = db.execute("SELECT source, rate_time FROM rates").fetchone()
            self.assertEqual(row["source"], "MOEX")
            self.assertEqual(row["rate_time"], "")
            # критично: после миграции PRIMARY KEY живёт — повтор не дублирует строку
            with rates.get_db() as db:
                rates.upsert_rate(db, "USD", "MOEX", "2026-11-01", "", 81.0)
                rows = db.execute("SELECT COUNT(*) c FROM rates").fetchone()["c"]
            self.assertEqual(rows, 1)


class TestUpsert(unittest.TestCase):

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")

    def test_upsert_replaces_same_moment(self):
        with rates.get_db() as db:
            rates.upsert_rate(db, "USD", "MOEX", "2026-11-02", "", 80.1)
            rates.upsert_rate(db, "USD", "MOEX", "2026-11-02", "", 80.5)
        rows = rates.app.test_client().get("/api/rates?currencies=USD").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate"], 80.5)

    def test_daily_and_intraday_coexist(self):
        """Один день: суточный фиксинг MOEX и две точки Yahoo — три строки."""
        with rates.get_db() as db:
            rates.upsert_rate(db, "USD", "MOEX", "2026-11-02", "", 80.5)
            rates.upsert_rate(db, "USD_OTC", "YAHOO", "2026-11-02", "09:00", 80.1)
            rates.upsert_rate(db, "USD_OTC", "YAHOO", "2026-11-02", "10:00", 80.9)
        rows = rates.app.test_client().get("/api/rates").json
        self.assertEqual(len(rows), 3)
        # сортировка по времени внутри дня
        otc = [r for r in rows if r["currency"] == "USD_OTC"]
        self.assertEqual([r["rate_time"] for r in otc], ["09:00", "10:00"])

    def test_upsert_skips_none(self):
        with rates.get_db() as db:
            self.assertFalse(rates.upsert_rate(db, "USD", "MOEX", "2026-11-02", "", None))


class TestMoexFetchHttpFallback(unittest.TestCase):
    """MOEX_FORCE_HTTP=1 переводит только iss.moex.com на http — и ничего больше."""

    def test_toggle(self):
        https = "https://iss.moex.com/iss/engines/currency/markets/selt/securities/CNYRUB_TOM.json"
        yahoo = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
        os.environ.pop("MOEX_FORCE_HTTP", None)
        self.assertEqual(rates.moex_url(https), https)
        os.environ["MOEX_FORCE_HTTP"] = "1"
        self.addCleanup(os.environ.pop, "MOEX_FORCE_HTTP", None)
        self.assertEqual(rates.moex_url(https), https.replace("https://", "http://", 1))
        self.assertEqual(rates.moex_url(yahoo), yahoo)   # yahoo не трогаем


class TestMoexFetch(unittest.TestCase):

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        patcher = mock.patch.object(rates, "http_get_json", side_effect=self.fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def fake_get(url, params=None):
        if "CNYRUB_TOM" in url:
            return MOCK_MOEX_LIVE_CNY
        if params is not None:
            return MOCK_MOEX_HISTORY          # история для backfill
        return MOCK_MOEX_LIVE                 # живой эндпоинт

    def test_fetch_live_usd(self):
        self.assertTrue(rates.fetch_live("USD"))
        row = rates.app.test_client().get("/api/rates?currencies=USD").json[0]
        self.assertEqual(row["source"], "MOEX")
        self.assertEqual(row["rate"], 80.5573)
        self.assertEqual(row["rate_date"], "2026-11-02")

    def test_backfill_usd(self):
        saved = rates.backfill("USD")
        self.assertGreaterEqual(saved, 2)             # 14-дневные окна могут повторять строки
        rows = rates.app.test_client().get("/api/rates?currencies=USD").json
        # …но в таблице благодаря PRIMARY KEY — ровно две уникальные даты
        self.assertEqual({r["rate_date"] for r in rows},
                         {"2026-10-31", "2026-11-01"})
        self.assertEqual(len(rows), 2)

    def test_backfill_windows_days(self):
        """Backfill ходит окнами ≤14 дней, иначе упрёмся в страницу из 100 строк."""
        calls = []
        def windowed(url, params=None):
            p = params or {}
            calls.append((p.get("from"), p.get("to")))
            return {"history": {"columns": ["TRADEDATE", "CLOSE"],
                                "data": [[p.get("to"), 80.0]]}}
        with mock.patch.object(rates, "http_get_json", side_effect=windowed):
            rates.backfill("USD", days=30)
        self.assertGreaterEqual(len(calls), 2)
        first_from = min(c[0] for c in calls)
        self.assertLessEqual(first_from, (date.today() - timedelta(days=30)).isoformat())
        self.assertEqual(max(c[1] for c in calls), date.today().isoformat())


class TestYahooFetch(unittest.TestCase):

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        patcher = mock.patch.object(rates, "http_get_json", return_value=_yahoo_payload())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fetch_live_yahoo_writes_intraday(self):
        self.assertTrue(rates.fetch_live("USD_OTC"))
        rows = rates.app.test_client().get("/api/rates?currencies=USD_OTC").json
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["source"] for r in rows}, {"YAHOO"})
        # время конвертировано из UTC в МСК (09:00 MSK = 06:00 UTC)
        self.assertEqual([r["rate_time"] for r in rows], ["09:00", "10:00"])
        self.assertEqual(rows[0]["rate_date"], "2026-11-02")

    def test_backfill_skips_yahoo(self):
        """У yahoo-валют нет истории ММВБ — backfill не должен дёргать сеть."""
        with mock.patch.object(rates, "http_get_json", side_effect=AssertionError):
            self.assertEqual(rates.backfill("USD_OTC"), 0)


class TestHourlyJobAndResilience(unittest.TestCase):

    def test_hourly_job_covers_every_group(self):
        """Прогон почасовой задачи обращается к каждой группе источников ровно раз."""
        touched = []
        mock.patch.object(rates, "fetch_live",
                          side_effect=lambda c: touched.append(c)).start()
        self.addCleanup(mock.patch.stopall)
        rates.hourly_job()
        # aliased-валюты (EUR_CBR, CNY_CBR) обслуживаются запросом USD_CBR
        expected = {c for c, s in rates.SOURCES.items() if "alias_of" not in s}
        self.assertEqual(set(touched), expected)
        for c in touched:
            self.assertEqual(touched.count(c), 1)      # без повторов

    def test_network_failure_is_graceful(self):
        """Биржа упала — приложение не падает, просто ничего не пишет."""
        def boom(url, params=None):
            raise ConnectionError("moex не отвечает")
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        with mock.patch.object(rates, "http_get_json", side_effect=boom):
            self.assertFalse(rates.fetch_live("USD"))
            self.assertFalse(rates.fetch_live("USD_OTC"))


class TestCbr(unittest.TestCase):
    """Официальный ЦБ: один XML на все валюты, windows-1251, запятая в числе."""

    XML_MOCK = (
        '<?xml version="1.0" encoding="windows-1251"?>'
        '<ValCurs Date="02.11.2026" name="Foreign Currency Market">'
        '<Valute ID="R01235"><CharCode>USD</CharCode><Nominal>1</Nominal>'
        '<VunitRate>80,5573</VunitRate></Valute>'
        '<Valute ID="R01239"><CharCode>EUR</CharCode><Nominal>1</Nominal>'
        '<VunitRate>90,6603</VunitRate></Valute>'
        '<Valute ID="R01375"><CharCode>CNY</CharCode><Nominal>1</Nominal>'
        '<VunitRate>12,0873</VunitRate></Valute>'
        '</ValCurs>'
    )

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        patcher = mock.patch.object(rates, "http_get_xml_text", return_value=self.XML_MOCK)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_request_writes_three_currencies(self):
        saved = rates.fetch_cbr_day(date(2026, 11, 2))
        self.assertEqual(saved, 3)
        rows = rates.app.test_client().get("/api/rates").json
        self.assertEqual({r["currency"] for r in rows},
                         {"USD_CBR", "EUR_CBR", "CNY_CBR"})
        self.assertTrue(all(r["source"] == "CBR" for r in rows))
        usd = [r for r in rows if r["currency"] == "USD_CBR"][0]
        self.assertAlmostEqual(usd["rate"], 80.5573)      # запятая съедена
        self.assertEqual(usd["rate_date"], "2026-11-02")  # ISO из «02.11.2026»

    def test_backfill_alias_skips_network(self):
        """EUR_CBR не должен дёргать ЦБ второй раз — его несёт USD_CBR."""
        with mock.patch.object(rates, "http_get_xml_text",
                               side_effect=AssertionError):
            self.assertEqual(rates.backfill("EUR_CBR"), 0)


class TestTopBanks(unittest.TestCase):
    """Рейтинг банков: семя при первом запуске + охраняемый секретом POST."""

    def setUp(self):
        os.environ["TOP_BANKS_SECRET"] = "test-secret"
        # каждый тест стартует с семенного среза: методы выполняются по
        # алфавиту, и test_post_* иначе «съедает» рейтинг у test_seeded_*
        with rates.get_db() as db:
            db.execute("DELETE FROM top_banks")
            for i, (name, assets, n1, profit, siber, cap123, vkl_fiz, sred_org) in enumerate(rates.TOP_BANKS_SEED, 1):
                db.execute("INSERT INTO top_banks (name, assets, n1, profit, siber, rank, grp,"
                           " cap123, vkl_fiz, sred_org) VALUES (?, ?, ?, ?, ?, ?, 'top', ?, ?, ?)",
                           (name, assets, n1, profit, siber, i, cap123, vkl_fiz, sred_org))
            for i, (name, assets, n1, profit, siber, place, cap123, vkl_fiz, sred_org) in enumerate(rates.TOP_BANKS_EXTRA, 1):
                db.execute("INSERT INTO top_banks (name, assets, n1, profit, siber, rank, grp, place,"
                           " cap123, vkl_fiz, sred_org) VALUES (?, ?, ?, ?, ?, ?, 'extra', ?, ?, ?, ?)",
                           (name, assets, n1, profit, siber, i, place, cap123, vkl_fiz, sred_org))
            db.execute("INSERT OR REPLACE INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, 'x')",
                       (rates.TOP_BANKS_AS_OF,))
        self.client = rates.app.test_client()

    def tearDown(self):
        os.environ.pop("TOP_BANKS_SECRET", None)

    def test_seeded_and_ordered(self):
        body = self.client.get("/api/top-banks").json
        top = [b for b in body["banks"] if b["grp"] == "top"]
        self.assertEqual(len(top), 10)
        self.assertEqual(top[0]["name"], "Сбербанк")
        self.assertEqual([b["rank"] for b in top], list(range(1, 11)))
        self.assertTrue(body["as_of"])

    def test_new_metrics_seeded(self):
        # капитал по ф.123, вклады физлиц и средства организаций — в трлн ₽
        top = self.client.get("/api/top-banks").json["banks"]
        sber = next(b for b in top if b["name"] == "Сбербанк")
        self.assertEqual((sber["cap123"], sber["vkl_fiz"], sber["sred_org"]), (8.1, 19.8, 15.7))
        novik = next(b for b in top if b["name"] == "Новикомбанк")
        self.assertEqual((novik["cap123"], novik["sred_org"]), (0.13, 1.16))
        psb = next(b for b in top if b["name"] == "Промсвязьбанк")
        self.assertIsNone(psb["vkl_fiz"])                # ф.101 не публикуется

    def test_post_accepts_optional_metrics(self):
        new = {"as_of": "01.03.2027", "banks": [
            {"name": "Банк А", "assets": 10.5, "n1": 12.0, "profit": 100,
             "cap123": 1.5, "vkl_fiz": 4.2, "sred_org": 3.3},
            {"name": "Банк Б", "assets": 9.0, "n1": 11.0, "profit": 90},
            {"name": "Банк В", "assets": 8.0, "n1": 10.0, "profit": 80, "cap123": None},
        ]}
        r = self.client.post("/api/top-banks", json=new, headers={"X-Admin-Token": "test-secret"})
        self.assertTrue(r.json["ok"])
        body = self.client.get("/api/top-banks").json
        self.assertEqual(body["banks"][0]["vkl_fiz"], 4.2)
        self.assertIsNone(body["banks"][1]["cap123"])   # не задано -> прочерк
        self.assertIsNone(body["banks"][2]["cap123"])   # явно null -> прочерк

    def test_extra_seeded(self):
        body = self.client.get("/api/top-banks").json
        extra = [b for b in body["banks"] if b["grp"] == "extra"]
        self.assertEqual([b["name"] for b in extra],
                         ["Новикомбанк", "ББР", "Хлынов", "Кузнецкий"])
        self.assertEqual(extra[0]["place"], 13)          # место по активам в РФ
        self.assertEqual(extra[3]["place"], 173)
        self.assertLess(extra[3]["profit"], 0)           # Кузнецкий — убыток

    def test_seed_is_rsbu_with_gaps(self):
        # семя — отчётность ЦБ по РСБУ; ПСБ форму 0409101 не публикует
        top = self.client.get("/api/top-banks").json["banks"]
        psb = next(b for b in top if b["name"] == "Промсвязьбанк")
        self.assertIsNone(psb["assets"])
        self.assertIsNone(psb["profit"])
        self.assertEqual(psb["cap123"], 1.0)
        sber = next(b for b in top if b["name"] == "Сбербанк")
        self.assertGreater(sber["assets"], 60)           # 68,8 трлн по ф.101

    def test_post_requires_secret_and_token(self):
        r = self.client.post("/api/top-banks", json={"as_of": "x", "banks": []})
        self.assertEqual(r.status_code, 401)                 # секрет задан, токен неверный
        os.environ.pop("TOP_BANKS_SECRET")
        r = self.client.post("/api/top-banks", json={})
        self.assertEqual(r.status_code, 503)                 # обновление выключено

    def test_post_rejects_garbage(self):
        h = {"X-Admin-Token": "test-secret"}
        self.assertEqual(self.client.post("/api/top-banks", json={"as_of": "01.12.2026",
                                                                  "banks": [{"name": "X"}]},
                                          headers=h).status_code, 400)   # слишком короткий список
        self.assertEqual(self.client.post("/api/top-banks", json={"banks": []},
                                          headers=h).status_code, 400)   # нет as_of
        bad = {"as_of": "01.12.2026", "banks": [
            {"name": "А", "assets": 1, "n1": 1, "profit": 1},
            {"name": "Б", "assets": 1, "n1": 1, "profit": 1, "grp": "bogus"},
            {"name": "В", "assets": 1, "n1": 1, "profit": 1}]}
        self.assertEqual(self.client.post("/api/top-banks", json=bad,
                                          headers=h).status_code, 400)   # неизвестная grp

    def test_post_replaces_snapshot(self):
        new = {"as_of": "01.12.2026", "banks": [
            {"name": "Банк А", "assets": 10.5, "n1": 12.0, "profit": 100, "siber": True},
            {"name": "Банк Б", "assets": 9.0,  "n1": 11.0, "profit": 90},
            {"name": "Банк В", "assets": 8.0,  "n1": 10.0, "profit": 80},
        ]}
        r = self.client.post("/api/top-banks", json=new, headers={"X-Admin-Token": "test-secret"})
        self.assertTrue(r.json["ok"])
        body = self.client.get("/api/top-banks").json
        self.assertEqual(body["as_of"], "01.12.2026")
        self.assertEqual([b["name"] for b in body["banks"]], ["Банк А", "Банк Б", "Банк В"])
        self.assertEqual(body["banks"][2]["rank"], 3)


class TestRatesEndpoint(unittest.TestCase):
    """Фильтрация по диапазону дат и валютам — то, что делает фронт."""

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
            for c, s, d, t, v in [
                ("USD", "MOEX", "2026-10-01", "", 78.0),
                ("USD", "MOEX", "2026-10-15", "", 80.0),
                ("USD", "MOEX", "2026-11-01", "", 81.0),
                ("USD_OTC", "YAHOO", "2026-10-15", "12:00", 80.2),
                ("CNY", "MOEX", "2026-10-15", "", 11.5),
            ]:
                db.execute("INSERT INTO rates VALUES (?,?,?,?,?,?)",
                           (c, s, d, t, v, "x"))
        self.client = rates.app.test_client()

    def test_no_filters_returns_all(self):
        self.assertEqual(len(self.client.get("/api/rates").json), 5)

    def test_from_to(self):
        rows = self.client.get("/api/rates?from=2026-10-10&to=2026-10-31").json
        self.assertEqual(len(rows), 3)              # USD 10-15, USD_OTC 10-15, CNY 10-15

    def test_only_from(self):
        rows = self.client.get("/api/rates?from=2026-10-31").json
        self.assertEqual({(r["currency"], r["rate_date"]) for r in rows},
                         {("USD", "2026-11-01")})

    def test_currency_filter(self):
        rows = self.client.get("/api/rates?currencies=CNY").json
        self.assertTrue(all(r["currency"] == "CNY" for r in rows))

    def test_unknown_currency_falls_back_to_all(self):
        self.assertEqual(len(self.client.get("/api/rates?currencies=ZWL").json), 5)


# Всемирный банк: [мета, строки]; строка = {date: 'Год', value: % или None}
MOCK_WB = [
    {"page": 1, "pages": 1, "total": 4},
    [
        {"date": "2025", "value": 8.7},
        {"date": "2024", "value": 8.486},
        {"date": "2026", "value": None},        # год ещё не закрыт
        {"date": "1993", "value": 874.2},
    ],
]


class TestInflation(unittest.TestCase):

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM inflation")

    def test_parse_skips_empty_values(self):
        """None-значения (текущий год) не пишутся; остальные — год и %."""
        with mock.patch.object(rates, "http_get_json", return_value=MOCK_WB):
            self.assertEqual(rates.fetch_inflation(), 3)
        with rates.get_db() as db:
            rows = {r["year"]: r["rate"] for r in
                    db.execute("SELECT year, rate FROM inflation")}
        self.assertEqual(rows, {2025: 8.7, 2024: 8.486, 1993: 874.2})

    def test_second_run_replaces_not_duplicates(self):
        with mock.patch.object(rates, "http_get_json", return_value=MOCK_WB):
            rates.fetch_inflation()
            self.assertEqual(rates.fetch_inflation(), 3)     # повтор безопасен
        with rates.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) n FROM inflation").fetchone()["n"], 3)

    def test_endpoint_ascending_and_filtered(self):
        with mock.patch.object(rates, "http_get_json", return_value=MOCK_WB):
            rates.fetch_inflation()
        rows = rates.app.test_client().get("/api/inflation").json
        self.assertEqual([r["year"] for r in rows], [1993, 2024, 2025])
        self.assertEqual(len(rates.app.test_client().get("/api/inflation?from=2000").json), 2)


class TestDbConnection(unittest.TestCase):
    """Одна SQLite-база на планировщик, seed и API: без ожидания блокировки
    часовой обход падает с «database is locked» (это и было багом)."""

    def test_busy_timeout_is_set(self):
        with rates.get_db() as db:
            self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 15000)

    def test_wal_requested_at_init(self):
        mode = rates.get_db().execute("PRAGMA journal_mode").fetchone()[0]
        # сетевой том может не потянуть WAL — тогда delete, но не «off»
        self.assertIn(mode.lower(), ("wal", "delete"))


if __name__ == "__main__":
    unittest.main()

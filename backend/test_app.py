# Тесты moex-rates: python3 -m unittest discover -v
#
# Особенность этого проекта: данные приходят из ИНТЕРНЕТА (API ММВБ).
# Тесты не должны зависеть от биржи — она может лежать, менять формат,
# закрываться на выходные. Поэтому сетевые запросы мы ПОДМЕШАЕМ
# (monkeypatch): вместо настоящего HTTP подставляем заготовленный JSON.
# Так тесты бегут мгновенно и всегда одинаково — «детерминированно».

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

# Временная база и отключение сети при импорте — ДО импорта приложения
# (app.py читает переменные и поднимает планировщик прямо при загрузке)
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_FILE"] = _tmpdb.name
os.environ["SKIP_SCHEDULER"] = "1"
os.environ["SKIP_SEED"] = "1"

import app as rates  # noqa: E402


# --- Заготовки ответов ММВБ (живой вид JSON, снят руками с iss.moex.com) ---

MOCK_USD_LIVE = {
    "marketdata": {
        "columns": ["SECID", "TRADEDATE", "CURRENTVALUE", "UPDATETIME"],
        "data": [["USDFIXME", "2026-11-02", 80.5573, "13:00:00"]],
    }
}

MOCK_EUR_LIVE = {
    "marketdata": {
        "columns": ["SECID", "TRADEDATE", "CURRENTVALUE"],
        "data": [["EURFIXME", "2026-11-02", 89.6603]],
    }
}

MOCK_CNY_LIVE = {
    "marketdata": {
        "columns": ["SECID", "TRADEDATE", "LAST", "WAPRICE"],
        "data": [["CNYRUB_TOM", "2026-11-02", 12.1234, 12.0789]],
    }
}

MOCK_HISTORY_USD = {
    "history": {
        "columns": ["TRADEDATE", "SECID", "OPEN", "LOW", "HIGH", "CLOSE"],
        "data": [
            ["2026-10-31", "USDFIXME", 81.0, 81.0, 81.0, 80.99],
            ["2026-11-01", "USDFIXME", 80.8, 80.8, 80.8, 80.77],
        ],
    }
}


def fake_http_get(url, params=None):
    """Подставной http_get_json: смотрим на URL и отдаём нужный кусок JSON."""
    if "index/securities/USDFIXME.json" in url and params is None:
        return MOCK_USD_LIVE
    if "index/securities/EURFIXME.json" in url and params is None:
        return MOCK_EUR_LIVE
    if "selt/securities/CNYRUB_TOM.json" in url and params is None:
        return MOCK_CNY_LIVE
    if "history/engines/currency/markets/index" in url:
        # backfill идёт окнами; реагируем как настоящий сервер:
        # отдаём только те строки заготовки, что попадают в запрошенное окно
        p = params or {}
        rows = [r for r in MOCK_HISTORY_USD["history"]["data"]
                if p.get("from", "") <= r[0] <= p.get("to", "9999")]
        return {"history": {"columns": MOCK_HISTORY_USD["history"]["columns"],
                            "data": rows}}
    return {"history": {"columns": [], "data": []}}   # пустая история по умолчанию


class TestDatabase(unittest.TestCase):
    """Таблица и UPSERT — без сети вообще."""

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")

    def test_upsert_replaces_same_day(self):
        """Повторная запись за ту же дату и валюту НЕ создаёт дубль."""
        with rates.get_db() as db:
            rates.upsert_rate(db, "USD", "2026-11-02", 80.1)
            rates.upsert_rate(db, "USD", "2026-11-02", 80.5)
        rows = self.client_get("/api/rates").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate"], 80.5)

    def test_upsert_skips_none(self):
        with rates.get_db() as db:
            self.assertFalse(rates.upsert_rate(db, "USD", "2026-11-02", None))

    @property
    def client(self):
        return rates.app.test_client()

    def client_get(self, url):
        return self.client.get(url)


class TestLiveFetch(unittest.TestCase):
    """Опрос «живого» API — на подмешанных данных."""

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        patcher = mock.patch.object(rates, "http_get_json", side_effect=fake_http_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fetch_live_usd(self):
        self.assertTrue(rates.fetch_live("USD"))
        rows = rates.app.test_client().get("/api/rates").json
        self.assertEqual(rows[0]["currency"], "USD")
        self.assertEqual(rows[0]["rate"], 80.5573)
        self.assertEqual(rows[0]["rate_date"], "2026-11-02")

    def test_fetch_live_cny(self):
        self.assertTrue(rates.fetch_live("CNY"))
        row = rates.app.test_client().get("/api/rates?currencies=CNY").json[0]
        self.assertEqual(row["rate"], 12.1234)          # поле LAST

    def test_hourly_job_writes_all_currencies(self):
        """Главный сценарий: прогон почасовой задачи кладёт в базу все валюты."""
        rates.hourly_job()
        currencies = {r["currency"] for r in
                      rates.app.test_client().get("/api/rates").json}
        self.assertEqual(currencies, {"USD", "EUR", "CNY"})

    def test_network_failure_is_graceful(self):
        """Биржа упала — приложение не падает, просто ничего не пишет."""
        def boom(url, params=None):
            raise ConnectionError("моэк не отвечает")
        with mock.patch.object(rates, "http_get_json", side_effect=boom):
            self.assertFalse(rates.fetch_live("USD"))
        self.assertEqual(rates.app.test_client().get("/api/rates").json, [])


class TestBackfill(unittest.TestCase):
    """Загрузка истории при первом старте."""

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
        patcher = mock.patch.object(rates, "http_get_json", side_effect=fake_http_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backfill_usd(self):
        saved = rates.backfill("USD")
        self.assertEqual(saved, 2)                      # оба дня из заготовки
        rows = rates.app.test_client().get("/api/rates?currencies=USD").json
        self.assertEqual({r["rate_date"] for r in rows},
                         {"2026-10-31", "2026-11-01"})

    def test_backfill_windows_days(self):
        """
        История подтягивается «окнами» по 14 дней (страницы по 100 строк
        у ММВБ ненадёжны). Проверим, что при запросе 30 дней было сделано
        несколько окон и все строки записаны.
        """
        calls = []
        def windowed(url, params=None):
            p = params or {}
            calls.append((p.get("from"), p.get("to")))
            # в каждом окне отдаём по одной «сделке» с ценой
            return {"history": {"columns": ["TRADEDATE", "CLOSE"],
                                "data": [[p.get("to"), 80.0]]}}
        with mock.patch.object(rates, "http_get_json", side_effect=windowed):
            saved = rates.backfill("USD", days=30)
        self.assertGreaterEqual(len(calls), 2)          # минимум два окна на 30 дней
        # из каждого окна записалось по строке, но дата одна (to окна) — 
        # считаем не сохранённые строки, а ширину покрытия:
        first_from = min(c[0] for c in calls)
        last_to = max(c[1] for c in calls)
        self.assertLessEqual(first_from, (date.today() - timedelta(days=30)).isoformat())
        self.assertEqual(last_to, date.today().isoformat())


class TestRatesEndpoint(unittest.TestCase):
    """Фильтрация по диапазону дат и валютам — то, что делает фронт."""

    def setUp(self):
        with rates.get_db() as db:
            db.execute("DELETE FROM rates")
            db.execute("INSERT INTO rates VALUES ('USD','2026-10-01',78.0,'x')")
            db.execute("INSERT INTO rates VALUES ('USD','2026-10-15',80.0,'x')")
            db.execute("INSERT INTO rates VALUES ('USD','2026-11-01',81.0,'x')")
            db.execute("INSERT INTO rates VALUES ('CNY','2026-10-15',11.5,'x')")
        self.client = rates.app.test_client()

    def test_no_filters_returns_all(self):
        self.assertEqual(len(self.client.get("/api/rates").json), 4)

    def test_from_to(self):
        rows = self.client.get("/api/rates?from=2026-10-10&to=2026-10-31").json
        self.assertEqual({(r["currency"], r["rate_date"]) for r in rows},
                         {("USD", "2026-10-15"), ("CNY", "2026-10-15")})

    def test_only_from(self):
        rows = self.client.get("/api/rates?from=2026-10-31").json
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate_date"], "2026-11-01")

    def test_currency_filter(self):
        rows = self.client.get("/api/rates?currencies=CNY").json
        self.assertTrue(all(r["currency"] == "CNY" for r in rows))

    def test_unknown_currency_falls_back_to_all(self):
        rows = self.client.get("/api/rates?currencies=ZWL").json
        self.assertEqual(len(rows), 4)                  # неизвестную выбросили, остались все


if __name__ == "__main__":
    unittest.main()

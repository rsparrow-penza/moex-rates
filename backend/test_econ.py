# Тесты блока «Экономика» (econ.py): парсеры файлов ЦБ/Росстата, эндпоинты,
# охраняемые POST. Сеть не трогаем — XLSX собираем in-memory, JSON ММВБ
# подмешиваем заготовками (сняты с живых ответов).

import io
import os
import tempfile
import unittest
from unittest import mock

_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("DATABASE_FILE", _tmpdb.name)
os.environ["SKIP_SCHEDULER"] = "1"
os.environ["SKIP_SEED"] = "1"

import app as rates      # noqa: E402
import econ              # noqa: E402


def make_xlsx(header_row, month_rows, sheet="ставка_руб"):
    """Мини-книга в памяти: строка 5 — подписи сроков, колонка A — месяцы."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["Средневзвешенные процентные ставки", None, None, None, None, None, None, None, None, None])
    ws.append(["(в целом по Российской Федерации)"] + [None] * 9)
    ws.append(["% годовых"] + [None] * 9)
    ws.append([None, None, None, None, None, None, None, None, None, None])
    ws.append(header_row)
    for r in month_rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


CORP_HEADER = [None, "до 30 дней, включая «до востребования»", "от 31 до 90 дней",
               "от 91 до 180 дней", "от 181 дня до 1 года", "до 1 года, включая «до востребования»",
               "от 1 года до 3 лет", "свыше 3 лет", "свыше 1 года", None]
CORP_ROWS = [
    ["Январь 2026", 17.0, 18.8, 19.4, 18.6, 18.3, 15.9, 14.9, 15.5, None],
    ["Февраль  2026", 16.4, 19.0, 19.0, 17.8, 17.9, 14.2, 15.5, 14.6, None],   # двойные пробелы — как в жизни
    ["Сентябрь 2026", 19.6, 17.3, 16.5, 16.5, 18.1, 12.1, 12.2, 12.1, None],
]


class TestCbrRatesParsers(unittest.TestCase):
    def test_parses_named_columns_by_index(self):
        content = make_xlsx(CORP_HEADER, CORP_ROWS)
        out = econ.parse_cbr_columns_sheet(content, {
            "rate_corp_short": (5, "до 1 года, включая"),
            "rate_corp_long": (8, "свыше 1 года")})
        self.assertEqual(out["rate_corp_short"]["2026-09"], 18.1)
        self.assertEqual(out["rate_corp_long"]["2026-01"], 15.5)
        self.assertEqual(len(out["rate_corp_short"]), 3)     # двойные пробелы в месяце не ломают разбор

    def test_column_layout_guard_raises(self):
        """Если ЦБ переставит колонки — парсер должен упасть, а не писать
        правые числа не в те ряды."""
        content = make_xlsx(CORP_HEADER, CORP_ROWS)
        with self.assertRaises(ValueError):
            econ.parse_cbr_columns_sheet(content, {"x": (1, "свыше 1 года")})

    def test_quarter_and_iso_periods(self):
        self.assertEqual(econ._period_from_label("Сентябрь 2026"), "2026-09")
        self.assertEqual(econ._period_from_label("Май      2022"), "2022-05")
        self.assertEqual(econ._period_from_label("2026-09"), "2026-09")
        self.assertEqual(econ._period_from_label("2026-09-15"), "2026-09-15")
        self.assertIsNone(econ._period_from_label("* Сноски"))


class TestOfz(unittest.TestCase):
    DATA = {"securities": {"columns": ["SECID", "SHORTNAME"],
                           "data": [[s, n] for s, n, _ in econ.OFZ_UNIVERSE]},
            "marketdata": {"columns": ["SECID", "YIELD"],
                           "data": [[s, 14.0 + i / 10] for i, (s, _n, _m) in enumerate(econ.OFZ_UNIVERSE)]}}

    FULL = {"history": {"columns": ["TRADEDATE", "SECID", "CLOSE", "YIELDCLOSE"],
                        "data": [[f"2026-09-{d:02d}", "SU26247RMFS5", 90.0, 15.5] for d in range(1, 101)]}}
    TAIL = {"history": {"columns": ["TRADEDATE", "SECID", "CLOSE", "YIELDCLOSE"],
                        "data": [["2026-10-01", "SU26247RMFS5", 90.1, 15.6]]}}

    def test_snapshot_written(self):
        with mock.patch.object(rates, "http_get_json", return_value=self.DATA):
            rows = econ.fetch_ofz_snapshot()
        self.assertEqual(len(rows), len(econ.OFZ_UNIVERSE))
        with rates.get_db() as db:
            got = db.execute("SELECT COUNT(*) n FROM ofz_snapshot").fetchone()["n"]
        self.assertEqual(got, len(econ.OFZ_UNIVERSE))

    def test_history_pages_until_short_page(self):
        calls = []

        def pages(url, params=None):
            calls.append(params.get("start"))
            # каждая-security: сначала полная страница (100), затем хвост
            return self.FULL if len(calls) % 2 else self.TAIL

        with mock.patch.object(rates, "http_get_json", side_effect=pages):
            n = econ.fetch_ofz_history(days=30)
        self.assertEqual(n, (100 + 1) * len(econ.OFZ_UNIVERSE))
        self.assertEqual(calls[:2], [0, 100])       # вторая страница запросилась со start
        with rates.get_db() as db:
            one = db.execute("SELECT COUNT(*) n FROM econ_series WHERE key = 'bond_yld_SU26247RMFS5'").fetchone()["n"]
        self.assertEqual(one, 101)


class TestEconEndpoints(unittest.TestCase):
    def test_series_unknown_key_404(self):
        r = rates.app.test_client().get("/api/econ/series?key=nope")
        self.assertEqual(r.status_code, 404)

    def test_meta_lists_registry_and_bonds(self):
        body = rates.app.test_client().get("/api/econ/meta").json
        self.assertIn("rate_corp_long", [m["key"] for m in body])
        self.assertTrue(any(m["key"].startswith("bond_yld_") for m in body))

    def test_manual_series_upsert(self):
        os.environ["TOP_BANKS_SECRET"] = "s3cret"
        h = {"X-Admin-Token": "s3cret"}
        c = rates.app.test_client()
        r = c.post("/api/econ/real", headers=h, json={
            "series": {"retail_turnover": {"2026-08": 3000.1, "2026-09": 3120.4}}})
        self.assertTrue(r.json["ok"])
        self.assertEqual(c.get("/api/econ/series?key=retail_turnover").json[-1]["value"], 3120.4)
        self.assertEqual(c.post("/api/econ/real", headers=h, json={"series": {"bogus": {}}}).status_code, 400)
        self.assertEqual(c.post("/api/econ/real", json={"series": {}}).status_code, 401)
        os.environ.pop("TOP_BANKS_SECRET")

    def test_budget_roundtrip(self):
        os.environ["TOP_BANKS_SECRET"] = "s3cret"
        h = {"X-Admin-Token": "s3cret"}
        c = rates.app.test_client()
        payload = {"as_of": "01.11.2026", "rows": [
            {"period": "2026-09", "revenue": 32100.0, "oil_gas": 7900.0, "non_oil": 24200.0,
             "expenditure": 34800.0, "deficit": -2700.0, "nd_deficit": -9800.0},
            {"period": "2026-10", "revenue": 35800.0, "expenditure": 38900.0, "deficit": -3100.0}]}
        self.assertTrue(c.post("/api/budget", json=payload, headers=h).json["ok"])
        body = c.get("/api/budget").json
        self.assertEqual(body["as_of"], "01.11.2026")
        self.assertEqual(len(body["rows"]), 2)
        self.assertEqual(body["rows"][0]["revenue"], 32100.0)
        self.assertNotIn("oil_gas", body["rows"][1])       # не задано — не пишем нулей
        self.assertEqual(c.post("/api/budget", json={"as_of": "x", "rows": []}, headers=h).status_code, 400)
        self.assertEqual(c.post("/api/budget", json=payload, headers={"X-Admin-Token": "no"}).status_code, 401)
        os.environ.pop("TOP_BANKS_SECRET")


class TestRosstatParsers(unittest.TestCase):
    def test_rddi_quarters_to_periods(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Содержание"
        ws2 = wb.create_sheet("РРДД_РДД")
        for _ in range(3):
            ws2.append([None] * 5)
        ws2.append([None, "Реальные ДД", None, "РРДД", None])
        ws2.append([None, "г/г", "к предш.", "г/г", "к предш."])
        ws2.append(["2025 год", None, None, None, None])
        ws2.append(["1 квартал", "108.3", "76.1", "107.1", "76.6"])
        ws2.append(["3 квартал", "108", "101.2", "107.7", "100"])
        ws2.append(["Год", "107.7", None, "107.4", None])
        ws2.append(["2026 год 4)", None, None, None, None])
        ws2.append(["1 квартал", "102.1", "73.3", "102.6", "74.3"])
        ws2.append(["3 квартал", "109.8", "102.7", "103,4 2)", "102.3"])   # значение со сноской и запятой
        buf = io.BytesIO()
        wb.save(buf)
        rows = econ.parse_rddi(buf.getvalue())
        d = dict(((p, v) for _k, p, v in rows))
        self.assertEqual(d["2025-03"], 107.1)
        self.assertEqual(d["2026-09"], 103.4)               # «103,4 2)» → 103.4
        self.assertNotIn("2025-12", d)                      # строки «Год» не рядовые

    def test_insecure_hosts_only_rosstat(self):
        self.assertTrue(econ._insecure("https://www.rosstat.gov.ru/x.xlsx"))
        self.assertFalse(econ._insecure("https://www.cbr.ru/x.xlsx"))
        self.assertFalse(econ._insecure("https://iss.moex.com/iss/x.json"))


if __name__ == "__main__":
    unittest.main()

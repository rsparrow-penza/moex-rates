import io
import os
import tempfile
import unittest
from unittest import mock

# своя база: PENZA-тесты пишут в econ_series и penza_meta, чужие им не соседи
os.environ.setdefault("DATABASE_FILE", os.path.join(tempfile.mkdtemp(), "penza-test.db"))

import openpyxl                                      # noqa: E402

os.environ["SKIP_SCHEDULER"] = "1"
os.environ["SKIP_SEED"] = "1"

import app as rates                                  # noqa: E402
import econ                                          # noqa: E402


def wb_bytes(sheets):
    """sheets: [(имя, [[клетки...], ...]), ...] → байты XLSX в памяти."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestPenzaCpi(unittest.TestCase):
    def test_regions_and_corridor(self):
        content = wb_bytes([("Содержание", [["Содержание:"]]),
                            ("10(2026)", [
                                ["К содержанию"],
                                ["Индексы потребительских цен"],
                                ["на конец периода, в % к предыдущему месяцу"],
                                ["Код территории", "Наименование территории", "ИПЦ",
                                 "в том числе:", None, None],
                                [None, None, None, "продовольственные товары",
                                 "непродовольственные товары", "услуги"],
                                ["643", "Российская Федерация", 100.99, 101.05, 99.83, 102.24],
                                ["33", "Приволжский федеральный округ", 100.98, 101.04, 99.84, 102.29],
                                ["56000000", "Пензенская область", 100.88, 101.06, 99.62, 102.22],
                                ["777", "Свердловская область", 101.0, 101.0, 101.0, 101.0],
                                ["999", "Мусор", 240.0, 100.0, 100.0, 100.0],   # вне коридора
                            ]),
                            ("Служебный", [["не месяц"]])])
        got = econ.parse_penza_cpi(content)
        self.assertEqual(set(got), {
            "pfo_cpi_mom", "region_cpi_mom", "rf_cpi_mom",
            # компоненты лежат на строку ниже заголовка — без этого ряда плиток
            # «продовольствие / непродовольственные / услуги» остались бы пустыми
            "region_food_mom", "region_nonfood_mom", "region_services_mom",
            "rf_food_mom", "rf_nonfood_mom", "rf_services_mom",
            "pfo_food_mom", "pfo_nonfood_mom", "pfo_services_mom"})
        self.assertEqual(got["region_food_mom"], {"2026-10": 101.06})
        self.assertEqual(got["region_services_mom"], {"2026-10": 102.22})
        self.assertEqual(got["region_cpi_mom"], {"2026-10": 100.88})
        # компоненты в парсере тоже есть — их несущие ключи region_food_mom и т.д.
        self.assertAlmostEqual(got["region_cpi_mom"]["2026-10"], 100.88)

    def test_yoy_needs_full_window(self):
        mom = {f"2025-{m:02d}": 100.0 + m / 100 for m in range(1, 13)}
        self.assertIn("2025-12", econ._yoy_from_mom(mom))
        without = dict(mom)
        del without["2025-06"]
        self.assertEqual(econ._yoy_from_mom(without), {})       # девять месяцев ≠ год

    def test_goods_codes_and_dots(self):
        content = wb_bytes([("09(2026)", [
            ["К содержанию"],
            ["Средние потребительские цены"],
            ["на конец периода"],
            ["Код территории", "Наименование территории", "1111", "1501", "7803"],
            [None, None, "Молоко питьевое", "Яйца куриные, 10 шт.", "Бензин АИ-95"],
            ["643", "Российская Федерация", 98.6, 93.93, 67.53],
            ["56000000000", "Пензенская область", 87.1, 66.31, 65.71],
            ["56401000000", "Пенза", 84.67, 69.69, "..."],       # городская цена закрыта
            ["36000000000", "Самарская область", 90.0, 90.0, 90.0],
        ]), ("Содержание", [["Содержание:"]])])
        got = econ.parse_penza_goods(content)
        self.assertEqual(got["pzc_eggs"], {"2026-09": 69.69})
        self.assertEqual(got["pzg_milk"], {"2026-09": 87.1})
        self.assertEqual(got["rfg_ai95"], {"2026-09": 67.53})
        self.assertNotIn("pzc_ai95", got)                        # «…» не превращается в цифру
        self.assertNotIn("rfg_milk_лишнее", got)

    def test_output_year_blocks(self):
        # годы стоят над январём своего блока: 2022 над колонкой 1, 2023 над 13
        head_year = [None, "2022"] + [None] * 11 + ["2023"] + [None] * 11
        head_month = [None] + [n for n in ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                                           "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь",
                                           "Декабрь")] * 2
        values = ["Пензенская область", 106.8, 106.4] + [None] * 10 + [99.0, 98.0] + [None] * 10
        pfo = ["Приволжский федеральный округ", 105.0, 105.1] + [None] * 10 + [97.0, 97.5] + [None] * 10
        content = wb_bytes([("1", [[None], ["Индекс выпуска товаров и услуг"],
                                   head_year, head_month,
                                   ["Белгородская область"] + [111.0] * 24, pfo, values]),
                            ("Содержание", [["Содержание:"]])])
        got = econ.parse_penza_output(content)
        self.assertEqual(got["region_output_yoy"]["2022-01"], 106.8)
        self.assertEqual(got["region_output_yoy"]["2023-01"], 99.0)     # не сдвинулось на 2022
        self.assertEqual(got["pfo_output_yoy"]["2022-02"], 105.1)


class TestPenzaFlat(unittest.TestCase):
    """Область и центр не должны меняться значениями — на этом парсер и споткнулся."""

    HEAD = [
        [None, "Средние цены на вторичном рынке жилья по субъектам Российской Федерации в 2026 году*"],
        [None, None, None, None, None, None, "на конец периода"],
        [None, "I квартал", None, None, None, None, "II квартал", None, None, None, None, "III квартал"],
        [None, "Все типы квартир", "из них:", None, None, None, "Все типы квартир", "из них:", None,
         None, None, "Все типы квартир"],
        [None, None, "низкого качества", "среднего", "улучшенного", "элитные", None,
         "низкого качества", "среднего", "улучшенного", "элитные", None],
    ]

    def test_region_and_center_separate(self):
        rf = ["643 - Российская Федерация", 143953.4, 1, 2, 3, 4, 146469.57, 1, 2, 3, 4, 149231.93]
        obl = ["56000000000 - Пензенская область", 92634.05, 1, 2, 3, 4, 94239.84, 1, 2, 3, 4, 91806.02]
        # в листе центра строка называется той же областью, но цифры относятся к Пензе
        cen = ["56000000000 - Пензенская область", 91469.31, 1, 2, 3, 4, 93304.38, 1, 2, 3, 4, 92874.72]
        content = wb_bytes([("Содержание", [["Содержание:"]]),
                            ("в целом по субъекту РФ", self.HEAD + [rf, obl]),
                            ("по центру субъекта РФ", self.HEAD + [rf, cen])])
        got = econ.parse_penza_flat(content, "vtor")
        self.assertEqual(got["region_flat_vtor"]["2026-03"], 92634.05)
        self.assertEqual(got["city_flat_vtor"]["2026-03"], 91469.31)
        self.assertEqual(got["rf_flat_vtor"]["2026-09"], 149231.93)
        self.assertNotIn("center", got)                          # ключей «на глаз» нет

    def test_unknown_territory_skipped(self):
        other = ["14000000000 - Белгородская область", 100469.99, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        content = wb_bytes([("в целом по субъекту РФ", self.HEAD + [other])])
        self.assertEqual(econ.parse_penza_flat(content, "vtor"), {})


class TestPenzaIncome(unittest.TestCase):
    def test_only_quarter_columns(self):
        years = [None, "2024 год", None, None, None, None, "2025 год", None, None, None, None, None,
                 "2026 год 3)", None, None]
        periods = [None, "I кв.", "II кв.", "III кв.", "IV кв.", "год",
                   "I кв.", "II кв.", "1 полугодие", "III кв.", "9 месяцев", "IV кв.",
                   "I кв.", "II кв.", "1 полугодие"]
        rows = [
            ["К содержанию"], ["Среднедушевые денежные доходы"],
            [None], [None], [None],
            years, periods,
            ["Российская Федерация", 60000, 62000, 64000, 66000, 64500,
             68000, 70000, 69000, 72000, 71000, 74000, 76000, 78000, 77000],
            ["Пензенская область", 40000, 41000, 42000, 43000, 42500,
             46064, 49739, 47901, 52997, 49600, 55674, 52110, 55238, 53674],
            ["Свердловская область", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        ]
        got = econ.parse_penza_income(wb_bytes([("СДД_субъекты", rows)]), "СДД", "sdd")
        self.assertEqual(got["region_income_sdd"], {"2024-03": 40000, "2024-06": 41000,
                                                   "2024-09": 42000, "2024-12": 43000,
                                                   "2025-03": 46064, "2025-06": 49739,
                                                   "2025-09": 52997, "2025-12": 55674,
                                                   "2026-03": 52110, "2026-06": 55238})
        # накопленные «1 полугодие» (47901) и «9 месяцев» (49600) в ряд не попали
        self.assertNotIn(47901.0, set(got["region_income_sdd"].values()))
        self.assertNotIn(49600.0, set(got["region_income_sdd"].values()))
        self.assertEqual(got["region_income_sdd"]["2025-06"], 49739.0)  # II кв., а не полугодие
        self.assertEqual(got["rf_income_sdd"]["2026-06"], 78000.0)


class TestPenzaResolver(unittest.TestCase):
    PAGE = ('<a href="/storage/mediabank/sred_cen_vtor_4kv-2026.xlsx">XLSX</a>'
            '<a href="/storage/mediabank/sred_cen_vtor_3kv-2026.xlsx">XLSX</a>'
            '<a href="/storage/mediabank/sred_cen_vtor_4kv-2025.xlsx">XLSX</a>')

    def test_freshness_order(self):
        self.assertGreater(econ._freshness(("3", "2026")), econ._freshness(("4", "2025")))
        self.assertGreater(econ._freshness(("10", "2026")), econ._freshness(("11", "2025")))

    def test_dead_link_is_skipped(self):
        """Ссылка на 4kv-2026 висит, файла нет (404) — берём следующий живой."""
        def fake(url, timeout=30):
            if "4kv-2026" in url:
                raise econ.requests.exceptions.HTTPError("404")
            return b"PK\x03\x04" + b"\0" * 100
        with mock.patch.dict(econ._page_cache, {econ.ROSSTAT_PAGES["price"]: self.PAGE}), \
             mock.patch.object(econ, "http_bytes", side_effect=fake):
            content, name = econ.resolve_penza_file("flat_vtor")
        self.assertEqual(name, "sred_cen_vtor_3kv-2026.xlsx")
        self.assertEqual(content[:2], b"PK")

    def test_not_an_xlsx_is_not_enough(self):
        """Заглушка Росстата тоже большая по размеру — верим подписи файла, а не длине."""
        with mock.patch.dict(econ._page_cache, {econ.ROSSTAT_PAGES["price"]: self.PAGE}), \
             mock.patch.object(econ, "http_bytes", return_value=b"<html>" * 100000):
            self.assertEqual(econ.resolve_penza_file("flat_vtor"), (None, None))

    def test_candidates_without_page(self):
        spec = econ.PENZA_FILES["flat_vtor"]
        with mock.patch.object(econ, "http_bytes", side_effect=econ.requests.ConnectionError):
            names = econ._penza_names("flat_vtor")
        self.assertTrue(all(n.startswith("sred_cen_vtor_") and n.endswith(".xlsx") for n in names))
        # без страницы имена собираются по шаблону от текущего квартала назад
        self.assertTrue(names[0].startswith("sred_cen_vtor_"), names[0])
        self.assertGreaterEqual(len(names), 6)


class TestPenzaRegistry(unittest.TestCase):
    """Реестр ожидаемых рядов: по нему сид решает, что блок недособран."""

    def test_keys_unique_and_counted(self):
        keys = econ.penza_expected_keys()
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 15 + 2 + 6 + 4 + len(econ.PENZA_GOODS) * 3)

    def test_parsers_only_produce_registered_keys(self):
        """Ни одного ряда, которого нет в списке, — иначе API его не отдаст."""
        cpi = econ.parse_penza_cpi(wb_bytes([("10(2026)", [
            ["К содержанию"], [None], [None],
            ["Код территории", "Наименование территории", "ИПЦ", "в том числе:", None, None],
            [None, None, None, "продовольственные товары", "непродовольственные товары", "услуги"],
            ["56000000", "Пензенская область", 100.88, 101.06, 99.62, 102.22]]),
            ("Содержание", [["Содержание:"]])]))
        self.assertTrue(set(cpi) <= set(econ.penza_expected_keys()), set(cpi) - set(econ.penza_expected_keys()))


class TestPenzaEndpoint(unittest.TestCase):
    def test_shape(self):
        with rates.get_db() as db:
            econ.init_econ_db()
            db.execute("INSERT OR REPLACE INTO econ_series VALUES ('region_cpi_mom','2026-10',100.88)")
            db.execute("INSERT OR REPLACE INTO econ_series VALUES ('rf_cpi_mom','2026-10',100.99)")
            db.execute("INSERT OR REPLACE INTO econ_series VALUES ('pzg_milk','2026-09',87.1)")
            db.execute("INSERT OR REPLACE INTO econ_series VALUES ('region_income_sdd','2026-06',55238)")
            db.execute("INSERT OR REPLACE INTO penza_meta VALUES ('as_of','2026-10')")
            db.execute("INSERT OR REPLACE INTO penza_meta VALUES ('goods_file','sred_potreb_cen_10-2026.xlsx')")
        b = rates.app.test_client().get("/api/penza").json
        self.assertEqual(b["as_of"], "2026-10")
        self.assertEqual(b["meta"]["goods_file"], "sred_potreb_cen_10-2026.xlsx")
        rows = {s["key"]: s for s in b["cpi"]}
        self.assertEqual(rows["region_mom"]["series"], [{"period": "2026-10", "value": 100.88}])
        self.assertEqual(rows["region_mom"]["unit"], "% / мес")
        self.assertEqual(rows["region_yoy"]["series"], [])   # г/г пока не из чего считать
        milk = [g for g in b["goods"] if g["key"] == "milk"][0]
        self.assertEqual(milk["region"][-1]["value"], 87.1)
        self.assertEqual(milk["city"], [])                       # пустой ряд = пустой список
        self.assertEqual([i["kind"] for i in b["income"]], ["sdd", "rdd"])
        self.assertEqual(len(b["goods"]), len(econ.PENZA_GOODS))

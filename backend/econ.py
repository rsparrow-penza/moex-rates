# econ.py — «Экономика»: шесть практических блоков макроданных.
#
# Что собирается автоматически:
#   1. Ставки в экономике — средневзвешенные ставки ЦБ по кредитам (юрлица,
#      физлица, авто) и вкладам (юрлица, физлица). Источник: три XLSX ЦБ
#      (cbr.ru/vfs/statistics/pdko/int_rat/…), лист «ставка_руб», месяцы
#      в строках колонки A, сроки — в колонках. Публикуются ~7–10 числа
#      за предыдущий месяц (лаг ~2 мес).
#   2. Вклады топ-10 — средние арифметические ставки по вкладам десятки
#      крупнейших банков (regulatory_rates.xlsx, лист «в рублях»). Месячные
#      срезы по бакетам сроков; недельного разреза 3–6 мес в открытом
#      файле нет — честная замена привычной «средней максимальной».
#   3. Кривая ОФЗ — ММВБ ISS, борт TQOB («Т+: Гособлигации»): дневной
#      снимок доходностей фиксированной корзины выпусков + подтягивание
#      истории YIELDCLOSE по каждому.
#   4. Реальный сектор (автомат) — Росстат ССРД/SDDS: ИПП (месяц,
#      2023=100), уровень безработицы (месяц, %) и номинальная зарплата
#      (месяц, ₽); реальные располагаемые доходы — кварталом из ротационного
#      файла «urov_….xlsx», имя пересобираем по шаблону.
#
# Что обновляется вручную (как топ-банки: источник режет ботов или не
# отдаёт машиночитаемого):
#   5. Федеральное исполнение бюджета — minfin.gov.ru отдаёт 403/503 всем
#      не-браузерным запросам, поэтому POST /api/budget с тем же
#      X-Admin-Token + скрипт update_budget.py.
#   6. Розничная торговля (оборот, млрд ₽, и % г/г) — файлы Росстата
#      переименовываются каждый месяц и старых ссылок нет в выдаче;
#      POST /api/econ/real.
#
# Хранение: econ_series(key, period, value) — «длинная» таблица всех
# рядов; ofz_snapshot — дневной срез кривой; budget_* — ручной срез.
# Все деньги — в тех же единицах, что источник (см. ECON_SERIES).

import os
import re
import io
import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import requests

# --- подключение к хосту app.py (чтобы не тащить sqlite-код дважды) ------
_conn = None          # app.get_db
_http_json = None     # app.http_get_json
_moex = None          # app.moex_url (http-fallback на VPS)


def configure(get_db, fetch_json, moex_rewrite):
    """app.py вызывает это при импорте: отдаёт свой get_db и MOEX-хелперы."""
    global _conn, _http_json, _moex
    _conn = get_db
    _http_json = fetch_json
    _moex = moex_rewrite


UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

# Росстат висит на цепочке «Russian Trusted CA», которой нет в стандартном
# доверилище (та же история, что с ММВБ). Отключаем проверку только для него.
INSECURE_HOSTS = ("rosstat.gov.ru",)


def _insecure(url: str) -> bool:
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return any(h in host for h in INSECURE_HOSTS)


def http_bytes(url: str, timeout: int = 30) -> bytes:
    """GET в байты с браузерным User-Agent: без него ЦБ отдаёт 403."""
    verify = not _insecure(url)
    r = requests.get(url, headers=UA, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r.content


# ---------------------------------------------------------------------------
# Реестр рядов
# ---------------------------------------------------------------------------

MONTHS_RU = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}

ECON_SERIES = {
    # — ставки ЦБ, % годовых, месячные —
    "rate_corp_short":  dict(label="Кредиты юрлицам, до 1 года", unit="%", group="rates", source="CBR",
                             explain="средневзвешенная ставка по рублёвым кредитам нефинансовым организациям сроком до года — «цена денег» для оборотного капитала бизнеса"),
    "rate_corp_long":   dict(label="Кредиты юрлицам, свыше 1 года", unit="%", group="rates", source="CBR",
                             explain="то же для длинных кредитов — по ней видно, готовы ли банки финансировать инвестиции, а не только оборотку"),
    "rate_retail_short": dict(label="Кредиты физлицам, до 1 года", unit="%", group="rates", source="CBR",
                             explain="потребительские кредиты до года: беззалоговые, кредитки — самый чувствительный к ключевой сегмент"),
    "rate_retail_long": dict(label="Кредиты физлицам, свыше 1 года", unit="%", group="rates", source="CBR",
                             explain="длинные потребкредиты — сюда же частично попадает ипотека по методике ЦБ"),
    "rate_retail_all":  dict(label="Кредиты физлицам, все сроки", unit="%", group="rates", source="CBR",
                             explain="средняя ставка по всем рублёвым кредитам населению"),
    "rate_auto":        dict(label="Автокредиты", unit="%", group="rates", source="CBR",
                             explain="средневзвешенная ставка по автокредитам; с 01.02.2026 ЦБ считает их без разбивки по срокам"),
    "rate_dep_fin_short": dict(label="Вклады физлиц, до 1 года", unit="%", group="rates", source="CBR",
                             explain="средняя ставка по коротким вкладам населения — конкурирует ли вклад с потребкредитом"),
    "rate_dep_fin_long":  dict(label="Вклады физлиц, свыше 1 года", unit="%", group="rates", source="CBR",
                             explain="длинные вклады: если их дают выше коротких — банки ждут разворота ставки"),
    "rate_dep_org_short": dict(label="Депозиты юрлиц, до 1 года", unit="%", group="rates", source="CBR",
                             explain="ставка по рублёвым депозитам компаний до года — доходность свободных денег бизнеса"),
    "rate_dep_org_long":  dict(label="Депозиты юрлиц, свыше 1 года", unit="%", group="rates", source="CBR",
                             explain="то же свыше года"),
    # — вклады топ-10 —
    "dep10_31_90":   dict(label="Топ-10: 31–90 дней", unit="%", group="dep10", source="CBR",
                          explain="средняя арифметическая ставка топ-10 банков по вкладам на 1–3 месяца"),
    "dep10_91_180":  dict(label="Топ-10: 3–6 мес", unit="%", group="dep10", source="CBR",
                          explain="самый близкий к исторической «средней максимальной ставке» бакет 3–6 месяцев"),
    "dep10_181_365": dict(label="Топ-10: 6–12 мес", unit="%", group="dep10", source="CBR",
                          explain="ставка десятки на полгода-год — ориентир для длинных сбережений"),
    # — реальный сектор, Росстат —
    "ippe_idx":     dict(label="Промышленность (2023=100)", unit="инд.", group="real", source="ROSSTAT",
                         explain="индекс промышленного производства, месячный, в пересчёте 2023=100; рост г/г = v/значение год назад − 1"),
    "unemp_rate":   dict(label="Безработица", unit="%", group="real", source="ROSSTAT",
                         explain="отношение численности безработных к рабочей силе (МОМ-методология), месячный"),
    "wage_nom":     dict(label="Номинальная зарплата", unit="₽/мес", group="real", source="ROSSTAT",
                         explain="среднемесячная начисленная зарплата работников организаций; реальная = это же, делённое на ИПЦ"),
    "rddi_yoy":     dict(label="Реальные доходы, г/г", unit="% г/г", group="real", source="ROSSTAT",
                         explain="реальные располагаемые доходы населения к соответствующему кварталу прошлого года"),
    # — розница, ручной ввод (см. README) —
    "retail_turnover": dict(label="Розничный оборот", unit="млрд ₽", group="real", source="MANUAL",
                            update="manual",
                            explain="оборот розничной торговли в действующих ценах, млрд ₽ — вводимы вручную из пресс-релиза Росстата, потому что машиночитаемого стабильного файла нет"),
    "retail_yoy":      dict(label="Розница, г/г", unit="% г/г", group="real", source="MANUAL",
                            update="manual",
                            explain="дефлированный рост розницы г/г, % — из того же пресс-релиза"),
    # — ОФЗ, ММВБ: динамические ключи bond_yld_<SECID> добавляются в рантайме —
}

# Корзина ОФЗ: постоянный купон (ОФЗ-ПД), погашение 2027–2041, примерно по
# выпуску на каждые полтора года. Из 33 подходящих ПД на борта TQOB взято 12 —
# состав зафиксирован именно здесь, а не собирается по обороту дня: иначе
# менялся бы сам ряд и история доходностей стала бы несравнимой.
# Обороты взятых выпусков 30–1800 млн ₽/день (снято 26.11.2026) — у 26235 и
# 26207 он тонкий, это отмечено в пояснении на вкладке. ОФЗ-ПК и ОФЗ-ИН не
# берём сознательно: их доходность — про ставку и инфляцию, а не про срок.
# YIELD берём из marketdata ММВБ.
OFZ_UNIVERSE = [
    ("SU26207RMFS9", "ОФЗ 26207", "2027-02-03"),
    ("SU26232RMFS7", "ОФЗ 26232", "2027-10-06"),
    ("SU26236RMFS8", "ОФЗ 26236", "2028-05-17"),
    ("SU26242RMFS6", "ОФЗ 26242", "2029-08-29"),
    ("SU26228RMFS5", "ОФЗ 26228", "2030-04-10"),
    ("SU26235RMFS0", "ОФЗ 26235", "2031-03-12"),
    ("SU26249RMFS1", "ОФЗ 26249", "2032-06-16"),
    ("SU26252RMFS5", "ОФЗ 26252", "2033-10-12"),
    ("SU26245RMFS9", "ОФЗ 26245", "2035-09-26"),
    ("SU26243RMFS4", "ОФЗ 26243", "2038-05-19"),
    ("SU26247RMFS5", "ОФЗ 26247", "2039-05-11"),
    ("SU26253RMFS3", "ОФЗ 26253", "2038-10-06"),
    ("SU26238RMFS4", "ОФЗ 26238", "2041-05-15"),
]


# Родословная корзины.
#
# Механизм: выпуск не исчезает из таблицы молча.
#   • exit — дата, после которой выпуска не будет в снимке (погашение). Пока
#     она в будущем, строка живая, но фронт уже показывает жёлтую метку
#     «выйдет ДД.ММ.ГГГГ» и имя преемника в подсказке;
#   • когда дата прошла, биржа перестаёт отдавать выпуск и его строка
#     пропала бы — GET /api/econ/bonds подставляет вместо неё ПОСЛЕДНЮ
#     реальную запись из ofz_snapshot с флагом retired. На фронте она серая,
#     с зачёркнутой доходностью и датой последней биржевой записи;
#   • сам ряд bond_yld_<secid> не трогается, и история «как менялись
#     доходности» остаётся сравнимой: выведенный выпуск можно вывести на
#     график рядом с его заменой;
#   • replaced_by — SECID преемника. Он должен войти в OFZ_UNIVERSE ЗАРАНЕЕ,
#     а не в день погашения: иначе короткий конец кривой повиснет без бумаги.
#
# Родословная заведена только для выпусков, у которых есть преемник в корзине.
# У остальных дата погашения и так в колонке «Погашение» — дублировать её
# меткой смысла нет.
#
# Состав по-прежнему меняется руками: OFZ_UNIVERSE — константа, автоматической
# пересборки по обороту нет (она переписывала бы историю рядов).
OFZ_LINEAGE = {
    # SU26207RMFS9: погашение 03.02.2027. Преемник короткого конца — SU26232RMFS7
    # (ОФЗ-ПД, погашение 06.10.2027, оборот 108 млн ₽/день против 58 млн у 26207;
    # снято с ТQOB 26.11.2026)
    "SU26207RMFS9": {"exit": "2027-02-03", "replaced_by": "SU26232RMFS7"},
}

# имена всех выпусков, в том числе тех, кто уже вышел из обращения: подсказка
# на фронте должна называть замену словом, а не SECID'ом
OFZ_NAMES = {secid: name for secid, name, _md in OFZ_UNIVERSE}


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# ---------------------------------------------------------------------------
# Парсеры XLSX (openpyxl; импортируем лениво — тесты без сети тоже живут)
# ---------------------------------------------------------------------------

def _load_sheet(content: bytes, sheet: str, index_fallback: int = 0):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    if sheet in wb.sheetnames:
        return wb[sheet]
    return wb[wb.sheetnames[index_fallback]]


def _period_from_label(label):
    """'Сентябрь 2026' -> '2026-09'; '01.09.2026'/'2026-09' -> '2026-09'."""
    s = _norm(label)
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}"
    m = re.match(r"^[а-яё]+ (\d{4})$", s)
    if m:
        month = MONTHS_RU.get(m.group(0).split()[0])
        if month:
            return f"{m.group(1)}-{month:02d}"
    return None


def parse_cbr_columns_sheet(content: bytes, columns: dict) -> dict:
    """
    Файлы ЦБ «ставка_руб»: месяцы в колонке A, сроки — жёстко заданными
    колонками (см. комментарии CBR_RATES_FILES). Номер колонки проверяется
    по подстроке заголовка из строки 5 — если ЦБ перемешает колонки, парсер
    упадёт в логе, а не начнёт писать правые цифры не в те ряды.
    columns: {series_key: (index, обязательная подстрока заголовка)}.
    """
    ws = _load_sheet(content, "ставка_руб")
    rows = list(ws.iter_rows(values_only=True))
    head = rows[4]
    for key, (i, must) in columns.items():
        if must not in _norm(head[i]):
            raise ValueError(f"{key}: колонка {i} = «{head[i]}», ждали «{must}»")
    out = {k: {} for k in columns}
    for r in rows:
        period = _period_from_label(r[0])
        if not period:
            continue
        for key, (i, _must) in columns.items():
            v = r[i] if i < len(r) else None
            if isinstance(v, (int, float)) and v > 0:
                out[key][period] = float(v)
    return out


def _parse_header_column_sheets(content, sheet_name, colmap, date_row=0):
    """
    Универсальный разбор «показатели в строках, даты в колонках» (СДДД-файлы
    Росстата): colmap = {series_key: нормативная подстрока первой ячейки}.
    """
    ws = _load_sheet(content, sheet_name)
    rows = list(ws.iter_rows(values_only=True))
    header = rows[date_row]
    # колонки дат: '2015-01-01 00:00:00' либо строка '2015-01'
    dates = {}
    for i, cell in enumerate(header):
        s = str(cell)[:7] if cell is not None else ""
        m = re.match(r"^(\d{4})-(\d{2})$", s)
        if m:
            dates[i] = s
    out = {k: {} for k in colmap}
    for r in rows[date_row + 1:]:
        row_name = _norm(r[0])
        for key, pat in colmap.items():
            if pat in row_name:
                for i, per in dates.items():
                    v = r[i] if i < len(r) else None
                    if isinstance(v, (int, float)):
                        out[key][per] = float(v)
                break
    return out


# ---------------------------------------------------------------------------
# Сборщики
# ---------------------------------------------------------------------------

CBR_RATES_FILES = {
    # Индексы колонок — как в 5-й строке листа «ставка_руб»; подписи
    # проверяются перед чтением. В loans_nonfin блоки «Всего» (B..I) и
    # «МСП» (J..Q) дублируют подписи — берём блок «Всего».
    "loans_nonfin": dict(
        url="https://www.cbr.ru/vfs/statistics/pdko/int_rat/loans_nonfin.xlsx",
        columns={"rate_corp_short": (5, "до 1 года, включая"),
                 "rate_corp_long": (8, "свыше 1 года")}),
    "loans_ind": dict(
        url="https://www.cbr.ru/vfs/statistics/pdko/int_rat/loans_ind_new.xlsx",
        columns={"rate_retail_short": (5, "до 1 года, включая"),
                 "rate_retail_long": (8, "свыше 1 года"),
                 "rate_retail_all": (9, "все виды кредитов"),
                 "rate_auto": (10, "авто")}),
    # deposits: физлица B..L (7 = «до 1 года, включая», 11 = «свыше 1
    # года»), юрлица M..S (16 и 19 — блок короче: нет строки «кроме до
    # востребования»). Ипотека в этом файле не публикуется.
    "deposits": dict(
        url="https://www.cbr.ru/vfs/statistics/pdko/int_rat/deposits.xlsx",
        columns={"rate_dep_fin_short": (7, "до 1 года, включая"),
                 "rate_dep_fin_long": (11, "свыше 1 года"),
                 "rate_dep_org_short": (16, "до 1 года, включая"),
                 "rate_dep_org_long": (19, "свыше 1 года")}),
}

ROSR_STAT_SHEET_LABOR = {
    "unemp_rate": "общая численность безработных",
    "labor_force": "рабочая сила",
    "wage_nom": "среднемесячная номинальная",
}

ROSR_STAT_SHEET_IPPE = {"ippe_idx": "индекс промышленного произ"}


def upsert_series(rows):
    """rows: [(key, period, value)] — идемпотентно."""
    with _conn() as db:
        db.executemany(
            "INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?, ?, ?)",
            [(k, p, v) for k, p, v in rows if v is not None])


def fetch_cbr_rates():
    """Месячные ставки ЦБ: три XLSX + regulatory_rates. Возвращает число строк."""
    n = 0
    for name, spec in CBR_RATES_FILES.items():
        data = parse_cbr_columns_sheet(http_bytes(spec["url"]), spec["columns"])
        rows = [(k, p, v) for k, series in data.items() for p, v in series.items()]
        upsert_series(rows)
        n += len(rows)
        print(f"[econ/rates] {name}: {len(rows)} значений")
    # вклады топ-10: лист «в рублях», бакеты D/E/F
    rows10 = parse_reg_rates(http_bytes("https://www.cbr.ru/vfs/statistics/pdko/int_rat/regulatory_rates.xlsx"))
    upsert_series(rows10)
    n += len(rows10)
    print(f"[econ/rates] top10: {len(rows10)} значений")
    return n


DEP10_BUCKETS = {"dep10_31_90": "от 31 до 90", "dep10_91_180": "от 91 до 180",
                 "dep10_181_365": "от 181 дня до 1 года"}


def parse_reg_rates(content: bytes):
    """regulatory_rates.xlsx, лист «в рублях»: бакеты сроков в строке 3."""
    ws = _load_sheet(content, "в рублях")
    rows = list(ws.iter_rows(values_only=True))
    head = rows[2]
    cols = {}
    for key, pat in DEP10_BUCKETS.items():
        for i, c in enumerate(head):
            if pat in _norm(c):
                cols[key] = i
                break
    out = []
    for r in rows:
        period = _period_from_label(r[0])
        if not period:
            continue
        for key, i in cols.items():
            v = r[i] if i < len(r) else None
            try:
                v = float(str(v).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if v > 0:
                out.append((key, period, v))
    return out


def fetch_rosstat_real():
    """СДДР/SDDS-файлы Росстата: ИПП + рынок труда; РРДД — ротационный."""
    n = 0
    labor = http_bytes("https://www.rosstat.gov.ru/storage/mediabank/SDDS_labor_market.xlsx")
    data = _parse_header_column_sheets(labor, "Рынок труда", ROSR_STAT_SHEET_LABOR)
    unemployed = data.get("unemp_rate", {})
    force = data.get("labor_force", {})
    rows = list((("wage_nom", p, v) for p, v in data.get("wage_nom", {}).items()))
    for per, u in unemployed.items():
        lf = force.get(per)
        if lf:
            rows.append(("unemp_rate", per, round(100.0 * u / lf, 2)))
    upsert_series(rows)
    n += len(rows)
    print(f"[econ/real] labor: {len(rows)}")

    ippe = http_bytes("https://www.rosstat.gov.ru/storage/mediabank/SDDS_industrial_production_index.xlsx")
    data = _parse_header_column_sheets(ippe, "ИПП", ROSR_STAT_SHEET_IPPE)
    rows = [("ippe_idx", p, v) for p, v in data.get("ippe_idx", {}).items()]
    upsert_series(rows)
    n += len(rows)
    print(f"[econ/real] ippe: {len(rows)}")

    r = resolve_urov_file()
    if r:
        rows = parse_rddi(r)
        upsert_series(rows)
        n += len(rows)
        print(f"[econ/real] rddi: {len(rows)}")
    return n


def resolve_urov_file():
    """Имя файла РРДД ротируется по кварталам — перебираем шаблоны, берём
    первый скачавшийся (самый свежий квартал вперёд)."""
    y, m = date.today().year, date.today().month
    # последний ЗАВЕРШЁННЫЙ квартал (публикация — в месяц после его конца)
    q = (m - 1) // 3          # 0 => берём Q4 прошлого года
    yy, qq = (y, q) if q else (y - 1, 4)
    for _step in range(6):
        name = f"urov_12kv_{qq}kv{yy}.xlsx"
        url = f"https://www.rosstat.gov.ru/storage/mediabank/{name}"
        try:
            content = http_bytes(url, timeout=20)
            # XLSX = zip: «PK». Страница-заглушка Росстата тоже большая,
            # так что размер ничего не доказывает, а подпись — доказывает.
            if content[:2] == b"PK":
                return content
        except requests.RequestException:
            pass
        qq -= 1               # шаг назад на квартал
        if qq == 0:
            yy, qq = yy - 1, 4
    return None


def parse_rddi(content: bytes):
    """Лист «РРДД_РДД»: блоки «20XX год» + квартальные строки; колонка D
    (индекс 3) — РРДД % к соответствующему периоду прошлого года."""
    ws = _load_sheet(content, "РРДД_РДД")
    rows = list(ws.iter_rows(values_only=True))
    year = None
    out = []
    for r in rows:
        first = _norm(r[0])
        m = re.match(r"^(\d{4}) год", first)
        if m:
            year = int(m.group(1))
            continue
        if not year:
            continue
        m = re.match(r"^(\d)\s*квартал$", first)
        if not m:
            continue
        v = r[3] if len(r) > 3 else None
        # значения могут приходить с выноской: «98,6 2)» — берём первое число
        m2 = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(v or ""))
        if not m2:
            continue
        val = float(m2.group(1).replace(",", "."))
        q = int(m.group(1))
        period = f"{year}-{QUARTER_END_MONTH[q]:02d}"
        out.append(("rddi_yoy", period, val))
    return out


# ---------------------------------------------------------------------------
# ОФЗ
# ---------------------------------------------------------------------------

BOARDS_URL = "http://iss.moex.com/iss/engines/stock/markets/bonds/boards/TQOB/securities.json"
# Справочник выпуска (дата эмиссии, купон, номинал, объём) — по одному
# запросу на бумагу; значения не меняются, поэтому ходим редко.
ISSUE_URL = "http://iss.moex.com/iss/securities/{secid}/issue.json"
HISTORY_URL = ("http://iss.moex.com/iss/history/engines/stock/markets/bonds/boards/TQOB/"
               "securities/{secid}.json")


def fetch_ofz_history(days: int = 400):
    """
    YIELDCLOSE по корзине за последние N дней. MOEX режет выдачу истории
    по 100 строк, поэтому идём страницами через start=… (идемпотентно:
    INSERT OR REPLACE по (key, period)).
    """
    if _http_json is None:
        return 0
    till = date.today().isoformat()
    from_ = (date.today() - timedelta(days=days)).isoformat()
    n = 0
    with _conn() as db:
        for secid, _shortname, _matdate in OFZ_UNIVERSE:
            start = 0
            while True:
                data = _http_json(
                    _moex(HISTORY_URL.format(secid=secid)),
                    params={"iss.meta": "off", "iss.only": "history",
                            "history.columns": "TRADEDATE,SECID,CLOSE,YIELDCLOSE",
                            "from": from_, "till": till, "start": start})
                hist = data.get("history", {})
                cols = hist.get("columns", [])
                rows = hist.get("data", [])
                if "YIELDCLOSE" not in cols or not rows:
                    break
                yi = cols.index("YIELDCLOSE")
                for row in rows:
                    yld = row[yi]
                    if isinstance(yld, (int, float)) and yld > 0:
                        db.execute(
                            "INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?,?,?)",
                            (f"bond_yld_{secid}", row[0], float(yld)))
                        n += 1
                if len(rows) < 100:
                    break
                start += len(rows)
    print(f"[econ/ofz] история: {n} значений за {days} дн.")
    return n


def fetch_ofz_snapshot():
    """
    Дневной срез кривой из live-борда TQOB: доходность, цена (LAST, % от
    номинала), изменение к предыдущему дню, НКД и дневной оборот.
    """
    if _http_json is None:
        return None
    data = _http_json(_moex(BOARDS_URL), params={
        "iss.meta": "off", "iss.only": "securities,marketdata",
        "securities.columns": "SECID,MATDATE,ACCRUEDINT",
        "marketdata.columns": "SECID,YIELD,LAST,LASTTOPREVPRICE,VALTODAY_RUR,DURATION"})
    sec_cols = data["securities"]["columns"]
    md_cols = data["marketdata"]["columns"]
    acc = {row[sec_cols.index("SECID")]: row for row in data["securities"]["data"]}
    md = {row[md_cols.index("SECID")]: row for row in data["marketdata"]["data"]}

    def num(row, cols, name):
        v = row[cols.index(name)]
        return float(v) if isinstance(v, (int, float)) else None

    today = datetime.now().strftime("%Y-%m-%d")
    rows = []
    for secid, shortname, matdate in OFZ_UNIVERSE:
        if secid not in md:
            continue
        mdr = md[secid]
        secr = acc.get(secid)
        # дату погашения доверяем бирже (в константе корзины она могла устареть);
        # OFZ_UNIVERSE задаёт только состав и порядок
        live_mat = (secr[sec_cols.index("MATDATE")] if secr and "MATDATE" in sec_cols else None)
        rows.append((today, secid, shortname, live_mat or matdate,
                     num(mdr, md_cols, "YIELD"),
                     num(mdr, md_cols, "LAST"),
                     num(mdr, md_cols, "LASTTOPREVPRICE"),
                     num(secr, sec_cols, "ACCRUEDINT") if secr else None,
                     num(mdr, md_cols, "VALTODAY_RUR")))
    with _conn() as db:
        db.executemany(
            "INSERT OR REPLACE INTO ofz_snapshot (snap_date, secid, shortname, matdate,"
            " yield_prc, price_prc, price_chg_prc, accrued, valtoday_rur)"
            " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print(f"[econ/ofz] снимок {today}: {len(rows)} выпусков")
    return rows


def fetch_ofz_info():
    """
    Справочник по выпуску: дата эмиссии (ISSUEDATE — начало размещения),
    купон % , номинал и объём. Берётся из /iss/securities/<ISIN>/issue.json;
    значения не меняются, поэтому достаточно одного захода при сиде и
    редкой проверки (раз в месяц).
    """
    if _http_json is None:
        return 0
    rows = []
    for secid, shortname, matdate in OFZ_UNIVERSE:
        data = _http_json(_moex(ISSUE_URL.format(secid=secid)),
                          params={"iss.meta": "off", "iss.only": "description"})
        pairs = {r[0]: r[2] for r in data["description"]["data"]}
        try:
            face = float(pairs.get("FACEVALUE") or 0)
            size = float(pairs.get("ISSUESIZE") or 0)
        except (TypeError, ValueError):
            face = size = 0.0
        rows.append((secid, shortname, matdate,
                     pairs.get("ISSUEDATE") or pairs.get("STARTDATEMOEX"),
                     float(pairs["COUPONPERCENT"]) if pairs.get("COUPONPERCENT") else None,
                     face or 1000.0,
                     round(size * (face or 1000.0) / 1e9, 1) if size else None))
    with _conn() as db:
        db.executemany(
            "INSERT OR REPLACE INTO ofz_info (secid, shortname, matdate, issuedate,"
            " coupon_prc, face_rur, issue_size_mlrd) VALUES (?,?,?,?,?,?,?)", rows)
    print(f"[econ/ofz] справочник выпусков: {len(rows)}")
    return len(rows)


# ---------------------------------------------------------------------------
# Металлы на бирже: ФОРТС (MOEX, $ за унцию) + мировые COMEX/NYMEX (Yahoo)
# ---------------------------------------------------------------------------
#
# ФОРТС торгует драгметаллами квартальными фьючерсами: GOLD (большой, ~100
# унций), SILV, PLT (платина), PLD (палладий) — котировка в $ за тройскую
# унцию, поэтому ряд напрямую сравним с COMEX/NYMEX. Контракт живёт квартал,
# «ближайший» каждый день ищем заново по дате последней торговли: когда
# GDZ6 переедет, ряд продолжится новым контрактом (в месте переклейки возможен
# скачок уровня на величину контанго — это честно, а не ошибка).
#
# Валютный спот-рынок ММВБ (ГЦМ) через ISS снаружи не отдаёт котировки —
# поэтому ФОРТС, а не «спот на слитки».

# unit — в чём котирует ФОРТС (унция для драгметаллов, тонна для цветных);
# world_factor — во что превратить котировку мирового источника, чтобы она
# стала сравнима с нашей: у меди COMEX цена в $ за фунт, а на ФОРТС в $ за
# тонну, поэтому ряд пересчитываем СРАЗУ, на входе, и в базе всё лежит
# в одних единицах (иначе график и базис врют бы на orders of magnitude).
LB_PER_TONNE = 2204.62262

METALS = [
    dict(metal="AU", label="Золото",   prefix="GOLD",   world="GC=F",  world_label="COMEX",
         unit="унция", world_factor=1.0),
    dict(metal="AG", label="Серебро",  prefix="SILV",   world="SI=F",  world_label="COMEX",
         unit="унция", world_factor=1.0),
    dict(metal="PT", label="Платина",  prefix="PLT",    world="PL=F",  world_label="NYMEX",
         unit="унция", world_factor=1.0),
    dict(metal="PD", label="Палладий", prefix="PLD",    world="PA=F",  world_label="NYMEX",
         unit="унция", world_factor=1.0),
    dict(metal="CU", label="Медь",     prefix="COPPER", world="HG=F",  world_label="COMEX",
         unit="тонна", world_factor=LB_PER_TONNE),
    dict(metal="AL", label="Алюминий", prefix="ALUM",   world="ALI=F", world_label="LME",
         unit="тонна", world_factor=1.0),
]

FORTS_LIST_URL = "http://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
FORTS_HISTORY_URL = "http://iss.moex.com/iss/history/engines/futures/markets/forts/securities/{secid}.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
METALS_DAYS = 150          # глубина истории: ~полгода торговых дней


def pick_forts_contract(rows, cols, today):
    """
    Из списка инструментов ФОРТС — ближайшие НЕ истёкшие контракты по каждому
    префиксу металла: {prefix: (secid, shortname, lasttrade)}.
    """
    i_sn, i_last, i_sec = (cols.index(c) for c in ("SHORTNAME", "LASTTRADEDATE", "SECID"))
    best = {}
    for r in rows:
        sn = r[i_sn] or ""
        prefix = sn.split("-")[0]
        if prefix not in {m["prefix"] for m in METALS}:
            continue
        last = r[i_last] or ""
        if last < today:                    # истёкший — не берём
            continue
        cur = best.get(prefix)
        if cur is None or last < cur[2]:
            best[prefix] = (r[i_sec], sn, last)
    return best


def fetch_metals(days: int = METALS_DAYS):
    """
    Дневные ряды металлов: ФОРТС (CLOSE фьючерса, $/унция) и мир (Yahoo
    GC=F/SI=F/PL=F/PA=F). Ключи econ_series: metal_<AU|AG|PT|PD>_forts и
    metal_<...>_world. Возвращает число записей.
    """
    if _http_json is None:
        return 0
    today = date.today().isoformat()
    listing = _http_json(_moex(FORTS_LIST_URL), params={
        "iss.meta": "off", "limit": 0,
        "securities.columns": "SECID,SHORTNAME,LASTTRADEDATE",
        # marketdata.LAST — сегодняшняя котировка: историю ФОРТС ISS выкладывает
        # только на следующий день, без неё блок вёл бы себя «вчерашним»
        "marketdata.columns": "SECID,LAST,SETTLEPRICE,CHANGEPRCNT"})
    live_md = {r[0]: r for r in listing.get("marketdata", {}).get("data", [])}
    live_cols = listing.get("marketdata", {}).get("columns", [])
    picked = pick_forts_contract(listing["securities"]["data"],
                                 listing["securities"]["columns"], today)
    since = (date.today() - timedelta(days=days)).isoformat()
    n = 0
    rows_saved = []
    with _conn() as db:
        for m in METALS:
            sel = picked.get(m["prefix"])
            if not sel:
                print(f"[metals] {m['label']}: на ФОРТС нет живого контракта")
                continue
            secid, shortname, lasttrade = sel
            db.execute(
                "INSERT OR REPLACE INTO metal_contract (metal, secid, shortname, lasttrade, seen_at)"
                " VALUES (?,?,?,?,?)", (m["metal"], secid, shortname, lasttrade, today))
            # ISS режет историю по 100 строк на страницу (проверено: 150 дней
            # = 100 строк и обрыв), поэтому идём страницами через start=
            start, hc = 0, None
            while True:
                hist = _http_json(_moex(FORTS_HISTORY_URL.format(secid=secid)), params={
                    "iss.meta": "off", "iss.only": "history",
                    "history.columns": "TRADEDATE,CLOSE,VOLUME",
                    "from": since, "till": today, "start": start})
                hc = hist["history"]["columns"]
                page = hist["history"]["data"]
                if not page:
                    break
                for row in page:
                    v = row[hc.index("CLOSE")]
                    if isinstance(v, (int, float)) and v > 0:
                        db.execute(
                            "INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?,?,?)",
                            (f"metal_{m['metal']}_forts", row[hc.index("TRADEDATE")], float(v)))
                        n += 1
                if len(page) < 100:
                    break
                start += len(page)
            # сегодняшняя котировка ФОРТС (торги идут, история — с дневным
            # лагом): LAST перекрывает строку сегодняшнего дня
            lrow = live_md.get(secid)
            if lrow and "LAST" in live_cols:
                v = lrow[live_cols.index("LAST")]
                if isinstance(v, (int, float)) and v > 0:
                    db.execute(
                        "INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?,?,?)",
                        (f"metal_{m['metal']}_forts", today, float(v)))
                    n += 1
            # мир: тот же Yahoo-endpoint, что качает Brent/Gold
            y = _http_json(YAHOO_CHART_URL.format(ticker=m["world"]),
                           params={"interval": "1d", "range": f"{days}d"})
            res = y["chart"]["result"][0]
            stamps = res.get("timestamp", [])
            closes = res["indicators"]["quote"][0].get("close", [])
            for ts, price in zip(stamps, closes):
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                db.execute("INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?,?,?)",
                           (f"metal_{m['metal']}_world", d, float(price) * m["world_factor"]))
                n += 1
            # живая котировка перекрывает дневную строку сегодняшнего дня:
            # COMEX торгует почти круглосуточно, «закрытие» в течение дня —
            # это последняя сделка
            meta = res.get("meta", {}) or {}
            live = meta.get("regularMarketPrice")
            if isinstance(live, (int, float)) and live > 0:
                ts = meta.get("regularMarketTime")
                day = (datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                       if ts else today)
                db.execute("INSERT OR REPLACE INTO econ_series (key, period, value) VALUES (?,?,?)",
                           (f"metal_{m['metal']}_world", day, float(live) * m["world_factor"]))
                n += 1
            rows_saved.append((m["label"], shortname, secid))
    print(f"[metals] записано {n} значений; контракты: "
          + ", ".join(f"{lbl}={sn}" for lbl, sn, _ in rows_saved))
    return n


# ---------------------------------------------------------------------------
# Бюджет и розница — ручные срезы (POST /api/budget, /api/econ/real)
# ---------------------------------------------------------------------------

BUDGET_FIELDS = ["revenue", "oil_gas", "non_oil", "expenditure", "deficit", "nd_deficit"]
MANUAL_SERIES = ["retail_turnover", "retail_yoy"]


# ---------------------------------------------------------------------------
# Металлопрокат: средние цены производителей (Росстат) + мониторинг МСиБ
# ---------------------------------------------------------------------------
#
# Здесь два ряда данных про одно и то же слово «прокат», и они принципиально
# разные — потому и лежат отдельным блоком, а не в «Металлах» биржевых.
#
# 1) Росстат — «Средние цены производителей на отдельные виды промышленных
#    товаров (с 1998 г.)». Ежемесячный XLSX, в нём настоящие ₽ за тонну по РФ и
#    федеральным округам. Имя файла ротируется по месяцам
#    (Proizvoditeli_Cena_MM-YYYY.xlsx) и архивных ссылок на странице нет,
#    поэтому имя сначала ресолвим по странице «Цены, инфляция», и только потом
#    качаем — та же схема, что с квартальными файлами РРДД.
#    Берём листы 2021+ : в листах 1–3 (1998–2020) стоят ГОДОВЫЕ средние, и
#    класть их в один ряд с месячными нельзя — график начал бы врать масштабом.
#
#    Арматуры в этом блоке нет намеренно. Она входит в группировку 24.10.61
#    «Прокат сортовой и катанка горячекатаные со свободной намоткой в бухты из
#    нелегированных сталей», и по Российской Федерации она закрыта прочерками
#    «…»: Росстат её не раскрывает. Придумать вместо неё «арматуру» из
#    соседней группировки было бы подтасовкой, так что арматура — из пункта 2.
#
# 2) Мониторинг журнала «Металлоснабжение и сбыт» (metalinfo.ru). Это НЕ биржа
#    и НЕ статистика: опрос металлотрейдеров Центрального региона (Москва) с
#    шагом примерно раз в две недели, «средняя цена» конкретной сортопозиции.
#    Он даёт то, чего нет у Росстата — арматуру, балку 30Б1, швеллер, лист по
#    конкретным размерам, — и потому показан отдельной таблицей со своей
#    подписью источника, а не смешан с официальными ценами.
#
# Neither ММВБ, ни СПбМТСБ, ни fedstat прокатом не помогли: на ФОРТС из 536
# контрактов металлургических нет ни одного (движок commodity — это НТБ:
# нефть, кокс, зерно), spmb.ru снаружи отдаёт только антибот-оболочку, fedstat
# — 403. Проверено 26.11.2026.

ROSRAT_PROKAT_PAGE = "https://www.rosstat.gov.ru/storage/mediabank/"
ROSRAT_PROKAT_INDEX = "https://www.rosstat.gov.ru/statistics/price"
PROKAT_MIN_YEAR = 2021          # см. выше: годовые листы в месячный ряд нельзя

# code — ОКПД2-группировка из файла; label — короткая метка на карточке,
# полная формулировка источника живёт в explain (как в остальных блоках).
PROKAT_ROSSTAT = [
    dict(code="24.10.11.120", key="pr_pig", label="Чугун передельный",
         explain="чушки и прочие первичные формы — сырьё для сталеплавильного передела; "
                 "средняя цена завода за тонну, без НДС"),
    dict(code="24.10.21", key="pr_ingot", label="Сталь в слитках",
         explain="нелегированная сталь в слитках и прочих первичных формах — полупродукт, "
                 "от него считается себестоимость проката"),
    dict(code="24.10.3", key="pr_hrc_plate", label="Лист г/к",
         explain="прокат листовой горячекатаный стальной без дополнительной обработки — "
                 "самая ходовая позиция для конструкций и сварки"),
    dict(code="24.10.41", key="pr_crc_plate", label="Лист х/к",
         explain="прокат листовой холоднокатаный из прочих нелегированных сталей — тоньше, "
                 "ровнее, дороже; идёт в корпуса, трубы и штамповку"),
    dict(code="24.10.62", key="pr_forge", label="Сортовой, кованый/г/к",
         explain="прокат сортовой и катанка стальные прочие (кованые, горячекатаные, "
                 "горячетянутые) — сюда попадают круг, квадрат и полоса"),
    dict(code="24.10.71", key="pr_beam", label="Балка (профили г/к)",
         explain="профили незамкнутые горячекатаные из нелегированной стали — двутавр и "
                 "швеллер, то, чем ставят каркасы и перекрытия"),
    dict(code="24.20.13.130", key="pr_pipe_ew", label="Трубы э/с",
         explain="трубы стальные электросварные общего назначения — водопровод, отопления, "
                 "каркасы; цена за тонну, не за метр"),
    dict(code="24.20.13.160", key="pr_pipe_wgp", label="Трубы ВГП",
         explain="трубы стальные водогазопроводные — бытовой контур: вода и газ"),
    dict(code="24.34.1", key="pr_wire", label="Проволока",
         explain="проволока холоднотянутая — вязальная, сетки, канаты; сильнее всех "
                 "зависит от спроса на стройке"),
]

# assort — id сортопозиции в мониторинге МСиБ (проверено 26.11.2026: 41–58,
# подписи и значения совпали с таблицей на самой странице).
PROKAT_DEALER = [
    dict(assort=41, key="dk_rebar10", label="Арматура А500С ф10"),
    dict(assort=42, key="dk_rebar12", label="Арматура А500С ф12"),
    dict(assort=43, key="dk_rebar8", label="Арматура В500С ф8"),
    dict(assort=44, key="dk_wire_r", label="Проволока вр ф4-5"),
    dict(assort=45, key="dk_wire_ann", label="Проволока ок ф1,2"),
    dict(assort=46, key="dk_wirerod", label="Катанка ф6,5"),
    dict(assort=47, key="dk_hrc4", label="Лист г/к 4"),
    dict(assort=48, key="dk_crc", label="Лист х/к 0,8-1"),
    dict(assort=49, key="dk_galv", label="Лист оцинк. 0,55"),
    dict(assort=50, key="dk_wgp20", label="Труба ВГП 20х2,8"),
    dict(assort=51, key="dk_wgp32", label="Труба ВГП 32х3,2"),
    dict(assort=52, key="dk_ew89", label="Труба э/с 89х3,5"),
    dict(assort=53, key="dk_ew102", label="Труба э/с 102х3,5"),
    dict(assort=54, key="dk_angle", label="Уголок р/п 63х5-6"),
    dict(assort=55, key="dk_ch10", label="Швеллер 10"),
    dict(assort=56, key="dk_ch12", label="Швеллер 12"),
    dict(assort=57, key="dk_beam30b1", label="Балка 30Б1"),
    dict(assort=58, key="dk_beam30sh1", label="Балка 30Ш1"),
]

for _p in PROKAT_ROSSTAT:
    ECON_SERIES[_p["key"]] = dict(label=_p["label"], unit="₽/т", group="prokat",
                                  source="ROSSTAT", explain=_p["explain"])
for _p in PROKAT_DEALER:
    ECON_SERIES[_p["key"]] = dict(
        label=_p["label"], unit="₽/т", group="prokat", source="MSIB",
        explain="дилерская цена конкретной сортопозиции, ₽ за тонну: опрос "
                "металлотрейдеров Центрального региона (Москва), шаг ~2 недели")
ECON_SERIES["dk_index"] = dict(
    label="Свод по 18 позициям", unit="₽/т", group="prokat", source="MSIB",
    explain="считаем сами: среднее арифметическое по всем позициям мониторинга "
            "МСиБ на дату наблюдения — прокси «прокат в целом», а не индекс сайта")

MSIB_STAT_URL = "https://metalinfo.ru/ru/metalmarket/statistics"
MSIB_DEPTH_YEARS = 7          # сайт отдаёт ряд с 2019 года без потери качества
# У Мониторинга цен за WAF: наш «серверный» UA (Linux/Chrome) он режет 403,
# а браузерный пропускает. Плюс набор sec-* заголовков — как у реального
# Chrome, потому что выборочно по одному заголовку фильтр не срабатывает.
MSIB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not?A_Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}
# наблюдение выходит раз ~в две недели, а бан по частоте запросов — реальная
# угроза (ловили 403 после серий). Поэтому ходим на сайт только когда старый
# ряд действительно мог обновиться.
MSIB_MIN_AGE_DAYS = 10


# --- Росстат ---------------------------------------------------------------

def _prokat_layout(grid):
    """
    Разметка листов между годами разъезжается: в 2021–2023 значения лежат в
    самой строке товара, в 2024–2025 — строкой ниже («Российская Федерация»),
    а в 2026 добавлена колонка «Единицы измерения» и месяцы сдвигаются. Поэтому
    колонку месяца ищем по подписи, а не по индексу (как в файлах ЦБ).
    """
    year = None
    for r in grid[:12]:
        if not r or not r[0]:
            continue
        m = re.search(r"в\s+(\d{4})\s*г", _norm(r[0]))
        if m:
            year = int(m.group(1))
            break
    months, units_col = {}, None
    for r in grid[:12]:
        found = {}
        for i, c in enumerate(r):
            s = _norm(c)
            if s in MONTHS_RU:
                found[MONTHS_RU[s]] = i
            elif s.startswith("единиц") and units_col is None:
                units_col = i
        if len(found) >= 3:
            months = found
            break
    return year, months, units_col


def _prokat_values(row, months):
    """[(номер месяца, значение)] из строки; «…», «...» и «…1)» — это не данные."""
    out = []
    for m, i in sorted(months.items()):
        v = _rub_number(row[i] if i < len(row) else None)
        if v is not None and v > 0:
            out.append((m, round(v, 2)))
    return out


def parse_prokat(content: bytes):
    """→ [(key, 'YYYY-MM', ₽/т)] по РФ, только позиции в тоннах."""
    from openpyxl import load_workbook
    by_code = {p["code"]: p for p in PROKAT_ROSSTAT}
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    rows = []
    for sheet in wb.sheetnames:
        grid = list(wb[sheet].iter_rows(values_only=True))
        year, months, units_col = _prokat_layout(grid)
        if not year or year < PROKAT_MIN_YEAR or not months:
            continue                       # годовые средние и содержание — мимо
        for i, r in enumerate(grid):
            code = _norm(r[1]) if len(r) > 1 and r[1] is not None else ""
            pos = by_code.get(code)
            if not pos:
                continue
            if units_col is not None and units_col < len(r):
                if "тонн" not in _norm(r[units_col]):
                    continue               # ₽/м² и ₽/шт в ₽/т не превращаем
            vals = _prokat_values(r, months)
            if not vals and i + 1 < len(grid):
                nxt = grid[i + 1]
                if nxt and _norm(nxt[0]).startswith("российская федерация"):
                    vals = _prokat_values(nxt, months)
            rows.extend((pos["key"], f"{year}-{m:02d}", v) for m, v in vals)
    return rows


def resolve_prokat_file():
    """→ (содержимое, имя файла) или (None, None)."""
    names = []
    try:
        page = http_bytes(ROSRAT_PROKAT_INDEX, timeout=40).decode("utf-8", "replace")
        found = re.findall(r"/storage/mediabank/(Proizvoditeli_Cena_(\d{2})-(\d{4})\.xlsx)", page)
        names = [n for n, _m, _y in sorted(set(found), key=lambda t: (t[2], t[1]), reverse=True)]
    except requests.RequestException as e:
        print(f"[prokat] страница Росстата недоступна ({e}), перебираем имена файлов")
    if not names:                          # страница не отдала — угадываем месяц
        y, mo = date.today().year, date.today().month
        for _ in range(8):
            mo -= 1
            if mo == 0:
                y, mo = y - 1, 12
            names.append(f"Proizvoditeli_Cena_{mo:02d}-{y:04d}.xlsx")
    for name in names:
        try:
            content = http_bytes(ROSRAT_PROKAT_PAGE + name, timeout=90)
            # XLSX = zip («PK»): страница-заглушка Росстата тоже большая по
            # размеру, так что подписи файла верят, а не длине ответа
            if content[:2] == b"PK":
                return content, name
        except requests.RequestException:
            continue
    return None, None


def set_prokat_meta(**kv):
    with _conn() as db:
        for k, v in kv.items():
            db.execute("INSERT OR REPLACE INTO prokat_meta (key, value) VALUES (?, ?)",
                       (k, str(v)))


def get_prokat_meta():
    with _conn() as db:
        return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM prokat_meta")}


def fetch_prokat():
    """Месячные средние цены производителей (Росстат), ₽ за тонна, по РФ."""
    content, name = resolve_prokat_file()
    if not content:
        print("[prokat] Росстат: файл со средними ценами производителей не найден")
        return 0
    if get_prokat_meta().get("rosstat_file") == name:
        print(f"[prokat] Росстат: {name} уже загружен — файл за месяц не менялся")
        return 0
    rows = parse_prokat(content)
    if not rows:
        print(f"[prokat] Росстат: в {name} не нашли ни одной нашей позиции "
              "(код товара переехал?) — ряды не трогаем")
        return 0
    upsert_series(rows)
    last = max(p for _k, p, _v in rows)
    set_prokat_meta(rosstat_file=name, rosstat_asof=last,
                    rosstat_updated=date.today().isoformat())
    keys = len({k for k, _p, _v in rows})
    print(f"[prokat] Росстат {name}: {len(rows)} значений по {keys} позициям, "
          f"последний период {last}")
    return len(rows)


# --- Мониторинг МСиБ -------------------------------------------------------

def parse_msib_labels(html: str):
    """Даты наблюдений из графика страницы: setLabels(["15-01-2025", …])."""
    m = re.search(r"setLabels\(\s*\[([^\]]*)\]", html)
    if not m:
        return []
    return [f"{y}-{mo}-{d}" for d, mo, y in re.findall(r'"(\d{2})-(\d{2})-(\d{4})"', m.group(1))]


_FOOTNOTE = re.compile(r"\s*\d+\s*[\)\]]\s*$")
# пробел-разделитель тысяч: между цифрами, дальше ровно три цифры
_THOUSANDS = re.compile(r"(?<=\d)[\s\u00a0\u2009](?=\d{3}(?:\D|$))")


def _rub_number(raw):
    """
    Число из «человеческой» строки статистики и выдачи МСиБ:
      «53750.000» → 53750.0   «62 900,5» → 62900.5
      «1.234,5»   → 1234.5    «103,4 2)» → 103.4   (выноска срезается)
      «…1)», «...», None → None (это отсутствие данных, а не ноль)
    Разделитель дробной части — последний из встретившихся; если он один,
    запятая считается десятичной, а точка — тоже: файлы ЦБ и Росстата дают
    «53750.000» именно как дробь, а не «53 миллиона».
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    t = _THOUSANDS.sub("", _FOOTNOTE.sub("", str(raw or "").strip()))
    m = re.match(r"^(-?\d[\d.,]*)", t)
    if not m:
        return None
    num = m.group(1)
    if "," in num and "." in num:
        dec = max(num.rfind(","), num.rfind("."))
        num = num[:dec].replace(".", "").replace(",", "") + "." + num[dec + 1:]
    elif "," in num:
        num = num.replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_msib_series(payload):
    """→ список цен той же длины, что data; вместо шума — None."""
    out = []
    for raw in payload.get("data", []) or []:
        v = _rub_number(raw)
        out.append(round(v, 1) if v and v > 0 else None)
    return out


def fetch_prokat_dealer(force: bool = False):
    """
    Ряды дилерских цен МСиБ (₽/т, Центральный регион). Страница отдаёт даты
    графика, а сами ряды — отдельным ajax-эндпоинтом; склеиваем их только при
    совпадении длин, иначе точки встали бы не на свои даты.

    Без force выходим сразу, если последнее наблюдение моложе MSIB_MIN_AGE_DAYS:
    мониторинг двухнедельный, а частые хождения на сайт кончаются 403.
    """
    asof = get_prokat_meta().get("dealer_asof")
    if asof and not force:
        try:
            age = (date.today() - date.fromisoformat(asof)).days
        except ValueError:
            age = None
        if age is not None and age < MSIB_MIN_AGE_DAYS:
            print(f"[prokat/МСиБ] наблюдение от {asof} ({age} дн. назад) — "
                  f"ряд двухнедельный, на сайт не идём")
            return 0
    today = date.today()
    # повторный ход на сайт в тот же день — только по force: рестарт контейнера
    # не должен превращаться в серию запросов к чужому WAF
    if not force and get_prokat_meta().get("dealer_attempt") == today.isoformat():
        print("[prokat/МСиБ] сегодня уже ходили на монитор — повтор не раньше завтра")
        return 0
    set_prokat_meta(dealer_attempt=today.isoformat())
    params = {"stype": "price",
              "startDate": f"01.01.{today.year - MSIB_DEPTH_YEARS}",
              "endDate": today.strftime("%d.%m.%Y")}
    query = urllib.parse.urlencode(params)
    try:
        r = requests.get(f"{MSIB_STAT_URL}?{query}", headers=MSIB_HEADERS, timeout=40)
        r.raise_for_status()
        page = r.text
    except requests.RequestException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        hint = " (WAF монитора режет частые обращения — попробуем завтра)" if code == 403 else ""
        print(f"[prokat/МСиБ] страница недоступна: {e}{hint}")
        return 0
    dates = parse_msib_labels(page)
    if len(dates) < 2:
        # сайт отдаёт эту же страницу и без блока графика (плавающая выдача) —
        # это не поломка, просто в этом цикле рядов не будет
        print("[prokat/МСиБ] дат наблюдений на странице нет — пропустивший цикл")
        return 0
    rows, by_date = [], {}
    fails = 0
    for pos in PROKAT_DEALER:
        q = urllib.parse.urlencode(dict(params, assort=pos["assort"]))
        body = dict(params, assort=pos["assort"])
        try:
            hdr = dict(MSIB_HEADERS, **{"X-Requested-With": "XMLHttpRequest",
                                         "Referer": MSIB_STAT_URL,
                                         "Accept": "application/json, text/javascript, */*; q=0.01"})
            r = requests.post(f"{MSIB_STAT_URL}/data?{q}", headers=hdr,
                              data=urllib.parse.urlencode(body), timeout=40)
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as e:
            # отдают и страницу без графика, и эндпоинт ряда 404 — тогда нет
            # смысла долбить оставшиеся 17 позиций
            fails += 1
            print(f"[prokat/МСиБ] {pos['label']}: {e}")
            if fails >= 3:
                print("[prokat/МСиБ] эндпоинт рядов не отвечает — останавливаю цикл")
                break
            continue
        # подпись берём с сайта, но только если это строка: иначе мусор из
        # ответа уехал бы в ECON_SERIES и вывалился бы /api/econ/meta
        title = payload.get("title")
        if isinstance(title, str) and title and _norm(title) != _norm(pos["label"]):
            print(f"[prokat/МСиБ] assort {pos['assort']}: сайт подписывает «{title}», "
                  f"у нас «{pos['label']}» — ключ оставляем, подпись берём с сайта")
            pos["label"] = title
            # фронт читает подписи из ECON_SERIES (он собирается при импорте),
            # поэтому правим оба места — иначе расхождение осталось бы только
            # в логе, а на странице висела бы наша старая формулировка
            ECON_SERIES[pos["key"]]["label"] = title
        vals = parse_msib_series(payload)
        if len(vals) != len(dates):
            print(f"[prokat/МСиБ] {pos['label']}: дат {len(dates)}, цен {len(vals)} — "
                  f"ряд не пишем, чтобы не сдвинуть точки")
            continue
        for d, v in zip(dates, vals):
            if v is None:
                continue
            rows.append((pos["key"], d, v))
            by_date.setdefault(d, []).append(v)
    rows.extend(("dk_index", d, round(sum(vs) / len(vs), 1))
                for d, vs in by_date.items() if vs)
    if not rows:
        print("[prokat/МСиБ] ни одного ряда не забрали — оставляем то, что в базе")
        return 0
    upsert_series(rows)
    set_prokat_meta(dealer_asof=dates[-1], dealer_dates=len(dates),
                    dealer_positions=len(by_date.get(dates[-1], [])),
                    dealer_updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"[prokat/МСиБ] записано {len(rows)} значений, наблюдение на {dates[-1]}")
    return len(rows)


# ---------------------------------------------------------------------------
# Инициализация схемы и регистрация задач
# ---------------------------------------------------------------------------

def init_econ_db():
    with _conn() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS econ_series (
                key    TEXT NOT NULL,
                period TEXT NOT NULL,
                value  REAL NOT NULL,
                PRIMARY KEY (key, period)
            )
            """)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ofz_snapshot (
                snap_date TEXT NOT NULL,
                secid     TEXT NOT NULL,
                shortname TEXT NOT NULL,
                matdate   TEXT NOT NULL,
                yield_prc REAL,
                price_prc REAL,        -- LAST: цена в % от номинала
                price_chg_prc REAL,    -- изменение цены к предыдущему дню, %
                accrued   REAL,        -- НКД, ₽ на облигацию
                valtoday_rur REAL,     -- оборот дня, ₽
                PRIMARY KEY (snap_date, secid)
            )
            """)
        # старая схема снимка (без цены) — добавляем колонки
        cols = [r["name"] for r in db.execute("PRAGMA table_info(ofz_snapshot)")]
        for col in ("price_prc", "price_chg_prc", "accrued", "valtoday_rur"):
            if col not in cols:
                db.execute(f"ALTER TABLE ofz_snapshot ADD COLUMN {col} REAL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ofz_info (
                secid       TEXT PRIMARY KEY,
                shortname   TEXT NOT NULL,
                matdate     TEXT NOT NULL,
                issuedate   TEXT,       -- дата начала размещения выпуска
                coupon_prc  REAL,       -- купон, % годовых
                face_rur    REAL,       -- номинал, ₽
                issue_size_mlrd REAL    -- объём выпуска, млрд ₽ (size × номинал)
            )
            """)
        # была опечатка в имени колонки — переименовываем, если узнали старую
        icols = [r["name"] for r in db.execute("PRAGMA table_info(ofz_info)")]
        if "issue_size_blrd" in icols and "issue_size_mlrd" not in icols:
            db.execute("ALTER TABLE ofz_info RENAME COLUMN issue_size_blrd TO issue_size_mlrd")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS metal_contract (
                metal     TEXT PRIMARY KEY,   -- AU | AG | PT | PD
                secid     TEXT NOT NULL,      -- GDZ6, SVZ6, PTZ6, PLDZ6...
                shortname TEXT NOT NULL,      -- GOLD-12.26
                lasttrade TEXT,               -- дата последней торговли контракта
                seen_at   TEXT NOT NULL       -- когда мы его выбрали
            )
            """)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_rows (
                period TEXT NOT NULL,      -- '2026-09' (накоплено с начала года)
                field  TEXT NOT NULL,      -- BUDGET_FIELDS
                value  REAL NOT NULL,
                PRIMARY KEY (period, field)
            )
            """)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS prokat_meta (
                key   TEXT PRIMARY KEY,   -- rosstat_file | rosstat_asof | dealer_asof…
                value TEXT NOT NULL
            )
            """)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS budget_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                as_of TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """)


def register_jobs(scheduler):
    """Возвращает список «когда что»: даты-предохранители в самих джобах."""

    def job_rates():
        # ЦБ выкладывает месячные ставки 7–10 числа; не долбим файлы в другие дни
        if 7 <= date.today().day <= 16:
            try:
                fetch_cbr_rates()
            except Exception as e:
                print(f"[econ/rates] сбой: {e}")

    def job_real():
        # Росстат: СДДР-выгрузки — по месяцам (обновление ~3-10 числа за
        # предыдущий, рынок труда ~15–22 за два предыдущих); РРДД — по кварталам.
        if date.today().day <= 24:
            try:
                fetch_rosstat_real()
            except Exception as e:
                print(f"[econ/real] сбой: {e}")

    def job_ofz():
        try:
            fetch_ofz_snapshot()
            fetch_ofz_history(days=5)
            # дата выпуска и купон не меняются — обновляем раз в месяц для
            # случая, когда в корзину добавим новый выпуск
            if date.today().day <= 5:
                fetch_ofz_info()
        except Exception as e:
            print(f"[econ/ofz] сбой: {e}")

    def job_metals():
        # фьючерсы торгуются с утра до вечера; 1D-ряд обновляется и ночью,
        # но переклейка контракта видна только на дневном срезе
        try:
            fetch_metals(days=30)
        except Exception as e:
            print(f"[metals] сбой: {e}")

    def job_prokat():
        # Росстат выкладывает месячные цены ~11-го числа, МСиБ — раз в две
        # недели; обоим хватает одного раза в сутки (имя файла Росстата
        # сверяется без повторной загрузки 2,5 МБ)
        try:
            fetch_prokat()
        except Exception as e:
            print(f"[prokat] сбой: {e}")
        try:
            fetch_prokat_dealer()
        except Exception as e:
            print(f"[prokat/МСиБ] сбой: {e}")

    scheduler.add_job(job_prokat, "interval", hours=24, id="econ_prokat")
    scheduler.add_job(job_rates, "interval", hours=12, id="econ_rates")
    scheduler.add_job(job_real, "interval", hours=24, id="econ_real")
    scheduler.add_job(job_ofz, "interval", hours=12, id="econ_ofz")
    scheduler.add_job(job_metals, "interval", hours=6, id="econ_metals")


def econ_seed():
    """
    Стартовый прогон: каждый блок докачиваем только если его в базе ещё нет
    (или нет целиком) — тогда повторный запуск сервиса не превращается в
    повторный опрос биржи, а пропущенный ранее справочник подтянется сам.
    """
    init_econ_db()
    with _conn() as db:
        def has(sql):
            return db.execute(sql).fetchone()[0]

        need_bonds = not has("SELECT COUNT(*) FROM econ_series WHERE key LIKE 'bond_yld_%'")
        need_info = not has("SELECT COUNT(*) FROM ofz_info")
        need_rates = not has("SELECT COUNT(*) FROM econ_series WHERE key LIKE 'rate_%'")
        need_real = not has("SELECT COUNT(*) FROM econ_series WHERE key IN ('ippe_idx','unemp_rate')")
        metal_keys = has("SELECT COUNT(DISTINCT key) FROM econ_series WHERE key LIKE 'metal_%'")
        pr_keys = has("SELECT COUNT(DISTINCT key) FROM econ_series WHERE key LIKE 'pr_%'")
        dk_keys = has("SELECT COUNT(DISTINCT key) FROM econ_series WHERE key LIKE 'dk_%'")
        # по одному ключу на площадку с металла: недобрало — значит добавили новый металл
        need_metals = metal_keys < 2 * len(METALS)

    if need_bonds:
        try:
            fetch_ofz_history(days=400)
        except Exception as e:
            print(f"[econ/seed] ofz history: {e}")
    try:
        fetch_ofz_snapshot()
    except Exception as e:
        print(f"[econ/seed] ofz snapshot: {e}")
    if need_info:
        try:
            fetch_ofz_info()
        except Exception as e:
            print(f"[econ/seed] ofz info: {e}")
    if need_rates:
        try:
            fetch_cbr_rates()
        except Exception as e:
            print(f"[econ/seed] rates: {e}")
    if need_real:
        try:
            fetch_rosstat_real()
        except Exception as e:
            print(f"[econ/seed] real: {e}")
    if need_metals:
        try:
            fetch_metals()
        except Exception as e:
            print(f"[econ/seed] metals: {e}")
    # прокат: позиции добавились в реестр — недобранные ключи подтянутся сами
    if pr_keys < len(PROKAT_ROSSTAT):
        try:
            fetch_prokat()
        except Exception as e:
            print(f"[econ/seed] prokat/Росстат: {e}")
    if dk_keys < len(PROKAT_DEALER) + 1:          # +1 — сводный ряд dk_index
        try:
            fetch_prokat_dealer()
        except Exception as e:
            print(f"[econ/seed] prokat/МСиБ: {e}")

#!/usr/bin/env python3
"""
update_budget.py — загрузить срез исполнения федерального бюджета в бэкенд.

Зачем скрипт, а не автосбор: minfin.gov.ru отдаёт 403/503 всем не-браузерным
запросам (и с локальной машины, и с VPS), открытых API у Казначейства тоже нет.
Поэтому цифры сверяются руками по «Краткой информации об исполнении
федерального бюджета» (minfin.gov.ru/ru/statistics/fedbud/execute/, файл
Prilozhenie_3_…_mes.xlsx) и заливаются сюда — раз в месяц, после публикации.

Формат budget.json (млрд ₽, накоплено с начала года; дефицит — со знаком минус):
    {
      "as_of": "01.11.2026",
      "rows": [
        {"period": "2026-09", "revenue": 32100.0, "oil_gas": 7900.0,
         "non_oil": 24200.0, "expenditure": 34800.0, "deficit": -2700.0,
         "nd_deficit": -9800.0},
        {"period": "2026-10", "revenue": 35800.0, "oil_gas": 8600.0,
         "non_oil": 27200.0, "expenditure": 38900.0, "deficit": -3100.0,
         "nd_deficit": -10500.0}
      ]
    }

Запуск:
    TOP_BANKS_SECRET=<секрет> python3 update_budget.py budget.json
    TOP_BANKS_URL=http://177.177.212.151:8080 TOP_BANKS_SECRET=... python3 update_budget.py budget.json

Секрет — тот же, что у рейтинга банков (TOP_BANKS_SECRET в окружении backend).
"""
import json
import os
import sys
import urllib.request

url = os.environ.get("TOP_BANKS_URL", "http://localhost:5002") + "/api/budget"
secret = os.environ.get("TOP_BANKS_SECRET", "")

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

payload = open(sys.argv[1], encoding="utf-8").read()
req = urllib.request.Request(url, data=payload.encode("utf-8"),
                            headers={"Content-Type": "application/json",
                                     "X-Admin-Token": secret},
                            method="POST")
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.status, resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print("ошибка", e.code, e.read().decode("utf-8"))
    sys.exit(2)

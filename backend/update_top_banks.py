#!/usr/bin/env python3
"""
update_top_banks.py — обновить срез «Топ-10 банков» на бэкенде.

Как пользоваться, когда ЦБ выпустит открытый указатель или когда цифры
сверены вручную: правите JSON-файл и отправляете его сюда.

    python3 update_top_banks.py banks.json            # localhost:5002
    TOP_BANKS_URL=http://177.177.212.151:8080 python3 update_top_banks.py banks.json

Формат banks.json (всё по отчётности ЦБ одного банка — РСБУ):
    {
      "as_of": "01.12.2026",
      "banks": [
        {"name": "Сбербанк", "assets": 68.8, "n1": 13.1, "profit": 1332.7,
         "siber": true, "grp": "top",
         "cap123": 8.1, "vkl_fiz": 19.8, "sred_org": 15.7},
        {"name": "ПСБ", "n1": 11.6, "siber": true, "cap123": 1.0},
        {"name": "Новикомбанк", "assets": 1.6, "n1": 13.0, "profit": 18.1,
         "cap123": 0.13, "vkl_fiz": 0.11, "sred_org": 1.16,
         "grp": "extra", "place": 13},
        ...
      ]
    }

Единицы: assets/cap123/vkl_fiz/sred_org — трлн ₽, profit — млрд ₽ с начала
года, n1 — %. grp — 'top' (рейтинг) или 'extra' (избранные), по умолчанию
'top'; place — место по активам нетто в РФ (рисуется только у extras);
n1 = 0 — нет данных, на фронте прочерк. rank проставляется по порядку в
группе. Любое денежное поле можно не указывать или дать null — так и надо
поступать с банками, которые не публикуют форму 0409101 (ПСБ).

Секрет — в переменной окружения TOP_BANKS_SECRET (та же, что на сервере):
без неё POST отвечает 503, с неверной — 401.
"""
import json
import os
import sys
import urllib.request

url = os.environ.get("TOP_BANKS_URL", "http://localhost:5002") + "/api/top-banks"
secret = os.environ.get("TOP_BANKS_SECRET", "")

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

with open(sys.argv[1], encoding="utf-8") as f:
    payload = f.read()

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

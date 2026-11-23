#!/usr/bin/env python3
"""
update_top_banks.py — обновить срез «Топ-10 банков» на бэкенде.

Как пользоваться, когда ЦБ выпустит открытый указатель или когда цифры
сверены вручную: правите JSON-файл и отправляете его сюда.

    python3 update_top_banks.py banks.json            # localhost:5002
    TOP_BANKS_URL=http://177.177.212.151:8080 python3 update_top_banks.py banks.json

Формат banks.json:
    {
      "as_of": "01.12.2026",
      "banks": [
        {"name": "Сбербанк", "assets": 63.9, "n1": 13.5, "profit": 1730, "siber": true},
        ...
      ]
    }

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

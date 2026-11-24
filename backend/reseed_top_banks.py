#!/usr/bin/env python3
"""Одноразовый рестарт среза top_banks на проде после апгрейда схемы.

Старая таблица не умеет NULL в assets/profit (NOT NULL), а семя в app.py
перезаписывается только когда таблица пуста. Поэтому: дамп семени из app.py,
пересоздание таблицы по новой DDL и заправка среза заново.

Использование:
    python3 reseed_top_banks.py /path/to/rates.db /path/to/backend/app.py
"""
import ast
import datetime
import re
import sqlite3
import sys


def block(app_src, var):
    """Вынуть литерал списка TOP_BANKS_* из исходника app.py."""
    s = "[" + app_src.split(var + " = [", 1)[1]
    depth = 0
    for i, ch in enumerate(s):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return eval(s[:i + 1])
    raise ValueError("не нашёл " + var)


def main(db_file, app_file):
    src = open(app_file, encoding="utf-8").read()
    seed = block(src, "TOP_BANKS_SEED")
    extra = block(src, "TOP_BANKS_EXTRA")
    as_of = re.search(r'TOP_BANKS_AS_OF = "([^"]+)"', src).group(1)
    print(f"семя: топ {len(seed)} + избранных {len(extra)}, as_of {as_of}")

    db = sqlite3.connect(db_file)
    db.executescript(
        """
        BEGIN;
        DROP TABLE IF EXISTS top_banks;
        CREATE TABLE top_banks (
            name TEXT PRIMARY KEY, assets REAL, n1 REAL NOT NULL, profit REAL,
            siber INTEGER NOT NULL, rank INTEGER NOT NULL,
            grp TEXT NOT NULL DEFAULT 'top', place INTEGER,
            cap123 REAL, vkl_fiz REAL, sred_org REAL
        );
        COMMIT;
        """
    )
    for i, r in enumerate(seed, 1):
        db.execute(
            "INSERT INTO top_banks (name, assets, n1, profit, siber, rank, grp, cap123, vkl_fiz, sred_org)"
            " VALUES (?, ?, ?, ?, ?, ?, 'top', ?, ?, ?)",
            (r[0], r[1], r[2], r[3], r[4], i, r[5], r[6], r[7]))
    for i, r in enumerate(extra, 1):
        db.execute(
            "INSERT INTO top_banks (name, assets, n1, profit, siber, rank, grp, place, cap123, vkl_fiz, sred_org)"
            " VALUES (?, ?, ?, ?, ?, ?, 'extra', ?, ?, ?, ?)",
            (r[0], r[1], r[2], r[3], r[4], i, r[5], r[6], r[7], r[8]))
    db.execute(
        "INSERT INTO top_banks_meta (id, as_of, updated_at) VALUES (1, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET as_of = excluded.as_of, updated_at = excluded.updated_at",
        (as_of, datetime.datetime.now().isoformat(timespec="seconds")))
    db.commit()
    for r in db.execute("SELECT name, assets, cap123, vkl_fiz, sred_org FROM top_banks ORDER BY grp DESC, rank"):
        print(r)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

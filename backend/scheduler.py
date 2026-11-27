"""Единственный production-процесс фонового сбора данных.

В web-контейнере SKIP_SCHEDULER=1 и SKIP_SEED=1: Gunicorn не должен создавать
свой APScheduler на каждый worker. Этот процесс импортирует app один раз —
при импорте app поднимаются scheduler и стартовый seed — и остаётся жить.
"""

import signal
import threading

import app  # noqa: F401,E402 — импорт запускает ровно один scheduler


stop = threading.Event()


def shutdown(*_args):
    scheduler = getattr(app, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    stop.set()


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

print("[scheduler] production scheduler запущен; web workers его не дублируют",
      flush=True)
stop.wait()
print("[scheduler] остановлен", flush=True)

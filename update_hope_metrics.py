#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автообновление HOPE-метрик из Huntflow.
  hope_time_to_hire / hope_voronka_naima / hope_nagruzka_rekrutera
Запуск:  HUNTFLOW_TOKEN=xxx python3 update_hope_metrics.py
Тест:    HUNTFLOW_MOCK=mock.json python3 update_hope_metrics.py
Токен — персональный API-токен Huntflow (Настройки → API), в GitHub Secrets.
Первый боевой запуск сверить глазами: скрипт печатает всё насчитанное до записи.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

API = "https://api.huntflow.ru/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "..", "data", "metrics.json")


def api(path):
    mock = os.environ.get("HUNTFLOW_MOCK")
    if mock:
        with open(mock, encoding="utf-8") as f:
            return json.load(f).get(path, {})
    token = os.environ.get("HUNTFLOW_TOKEN")
    if not token:
        sys.exit("Нет HUNTFLOW_TOKEN (или HUNTFLOW_MOCK для теста)")
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    accounts = api("/accounts").get("items", [])
    if not accounts:
        sys.exit("Huntflow не вернул аккаунтов — проверить токен")
    acc = accounts[0]["id"]

    vacancies = api(f"/accounts/{acc}/vacancies?count=100&opened=false").get("items", [])
    open_vac = [v for v in vacancies if v.get("state") == "OPEN"]
    closed = [v for v in vacancies if v.get("state") == "CLOSED"]

    tth = []
    for v in closed:
        c, f = v.get("created"), v.get("closed") or v.get("updated")
        if c and f:
            try:
                d = (datetime.fromisoformat(f[:19]) - datetime.fromisoformat(c[:19])).days
                if 0 < d < 365:
                    tth.append(d)
            except ValueError:
                pass
    avg_tth = round(sum(tth) / len(tth)) if tth else None

    recruiters = None
    try:
        cw = api(f"/accounts/{acc}/coworkers?count=100").get("items", [])
        rec = [c for c in cw if "recruiter" in str(c.get("member_type", "")).lower()]
        recruiters = len(rec) or None
    except Exception:
        pass
    load = round(len(open_vac) / recruiters, 1) if recruiters else None

    conv = None
    try:
        statuses = api(f"/accounts/{acc}/vacancies/statuses").get("items", [])
        hired_ids = [s["id"] for s in statuses if s.get("type") == "hired"]
        total = api(f"/accounts/{acc}/applicants?count=1").get("total_items")
        hired = 0
        for hid in hired_ids:
            hired += api(f"/accounts/{acc}/applicants?count=1&status={hid}").get("total_items", 0)
        if total:
            conv = round(100.0 * hired / total, 2)
    except Exception as e:
        print("воронка: не посчиталась —", e)

    values = {}
    if avg_tth is not None:
        values["hope_time_to_hire"] = avg_tth
    if conv is not None:
        values["hope_voronka_naima"] = conv
    if load is not None:
        values["hope_nagruzka_rekrutera"] = load

    print("Насчитано:", json.dumps({
        "вакансий открыто": len(open_vac), "закрыто": len(closed),
        "time_to_hire": avg_tth, "конверсия %": conv,
        "рекрутёров": recruiters, "нагрузка": load}, ensure_ascii=False))

    if not values:
        sys.exit("Нечего записывать — проверить парсинг по факту API")

    data = {"updated": today, "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)

    data["updated"] = today
    data["values"].update(values)
    data["meta"]["hope_source"] = f"Huntflow API, снято {now.strftime('%Y-%m-%d %H:%M UTC')}"
    for key, val in values.items():
        hist = [p for p in data["history"].get(key, []) if p["d"] != today]
        hist.append({"d": today, "v": val})
        data["history"][key] = sorted(hist, key=lambda p: p["d"])[-90:]

    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("OK: записано в data/metrics.json")


if __name__ == "__main__":
    main()

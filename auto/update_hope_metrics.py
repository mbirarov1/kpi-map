#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автообновление HOPE-метрик из Huntflow. Версия 2 — под реальную структуру аккаунта Кайтен
(проверено 17.08.2026: 8069 кандидатов, 6 открытых вакансий, 94 закрытых, 22 статуса воронки).

Считает:
  hope_vacancies_open     — открытых вакансий сейчас
  hope_vacancies_overdue  — открытых вакансий с просроченным дедлайном
  hope_funnel_active      — кандидатов в активной воронке (все рабочие этапы, кроме отказа/резерва)
  hope_time_to_hire       — медиана дней от появления кандидата на вакансии до статуса «Вышел на работу»
  hope_offer_to_hire      — конверсия «Оффер принят» → «Вышел на работу», %
  hope_nagruzka_rekrutera — открытых вакансий на одного рекрутёра

Чего в Huntflow НЕТ и считать нельзя: cost per hire (расходы на источники не хранятся),
зарплата лежит текстом («до 350к») — арифметике не поддаётся.

Запуск:  HUNTFLOW_TOKEN=xxx python3 update_hope_metrics.py
Тест:    HUNTFLOW_MOCK=mock.json python3 update_hope_metrics.py
Скрипт печатает всё, что насчитал, ДО записи — первый боевой прогон сверять глазами.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.huntflow.ru/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "..", "data", "metrics.json")

TTH_SAMPLE = 30          # сколько последних наймов берём для медианы time to hire
# Этапы, которые НЕ считаются активной воронкой (по названию, регистр не важен)
NOT_ACTIVE = ("отказ", "резерв", "увольнение", "вышел на работу", "ис закрыт")


def api(path, quiet=False):
    """GET к Huntflow. Возвращает dict или None (с печатью причины)."""
    mock = os.environ.get("HUNTFLOW_MOCK")
    if mock:
        with open(mock, encoding="utf-8") as f:
            return json.load(f).get(path, {})
    token = os.environ.get("HUNTFLOW_TOKEN")
    if not token:
        sys.exit("Нет HUNTFLOW_TOKEN (или HUNTFLOW_MOCK для теста)")
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "User-Agent": "kpi-board/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("401: токен недействителен или истёк. Обновить HUNTFLOW_TOKEN "
                     "(refresh лежит в HUNTFLOW_REFRESH_TOKEN, обменять на новый вручную).")
        if not quiet:
            print(f"  ! {path} → HTTP {e.code}")
        return None
    except Exception as e:
        if not quiet:
            print(f"  ! {path} → {e}")
        return None


def d(iso):
    if not iso:
        return None
    s = str(iso).replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def median(a):
    a = sorted(a)
    return a[len(a) // 2] if a else None


def main():
    now = datetime.now(timezone.utc)
    today = now.date()

    # --- аккаунт ---
    acc_resp = api("/accounts") or {}
    accounts = acc_resp.get("items", [])
    if not accounts:
        sys.exit("Huntflow не вернул аккаунтов — проверить токен и права")
    acc = accounts[0]["id"]
    print(f"Аккаунт: {accounts[0].get('name')} (id {acc})")

    # --- статусы воронки ---
    st = api(f"/accounts/{acc}/vacancies/statuses") or {}
    statuses = [s for s in st.get("items", []) if isinstance(s.get("id"), int)]
    print(f"Статусов: {len(statuses)} → " + " · ".join(
        f"{s['name']}[{s.get('type')}]" for s in statuses))
    by_type = lambda t: [s for s in statuses if s.get("type") == t]
    hired_ids = [s["id"] for s in by_type("hired")]
    active_statuses = [s for s in statuses
                       if s.get("type") == "user"
                       and not any(w in s["name"].lower() for w in NOT_ACTIVE)]

    # --- вакансии ---
    def vacancies(state):
        out, page = [], 1
        while page <= 10:
            r = api(f"/accounts/{acc}/vacancies?count=100&page={page}&state={state}")
            if not r:
                break
            items = r.get("items", [])
            out += items
            if len(items) < 100:
                break
            page += 1
        return out

    open_vac = vacancies("OPEN")
    closed_vac = vacancies("CLOSED")
    print(f"Вакансии: открытых {len(open_vac)}, закрытых {len(closed_vac)}")

    overdue = []
    for v in open_vac:
        dl = d(v.get("deadline"))
        if dl and dl.date() < today:
            overdue.append(v.get("position"))

    # --- воронка: сколько кандидатов на каждом активном этапе ---
    funnel, funnel_total = [], 0
    for s in active_statuses:
        r = api(f"/accounts/{acc}/applicants?count=1&status={s['id']}", quiet=True)
        n = (r or {}).get("total_items")
        if n is None:
            continue
        funnel.append((s["name"], n))
        funnel_total += n
    if funnel:
        print("Воронка: " + " · ".join(f"{n}—{c}" for n, c in funnel) + f" | всего {funnel_total}")

    # --- оффер принят → вышел на работу ---
    def count_by_name(part):
        for s in statuses:
            if part in s["name"].lower():
                r = api(f"/accounts/{acc}/applicants?count=1&status={s['id']}", quiet=True)
                return (r or {}).get("total_items")
        return None

    offers = count_by_name("оффер принят")
    hired_total = None
    if hired_ids:
        hired_total = 0
        for hid in hired_ids:
            r = api(f"/accounts/{acc}/applicants?count=1&status={hid}", quiet=True)
            hired_total += (r or {}).get("total_items", 0)
    offer_to_hire = None
    if offers is not None and hired_total:
        base = offers + hired_total
        offer_to_hire = round(100.0 * hired_total / base, 1) if base else None
    print(f"Офферов принято сейчас: {offers} · всего вышло на работу: {hired_total}")

    # --- time to hire по логам нанятых ---
    tth = []
    if hired_ids:
        hired_list = []
        for hid in hired_ids:
            r = api(f"/accounts/{acc}/applicants?count={TTH_SAMPLE}&status={hid}", quiet=True)
            hired_list += (r or {}).get("items", [])
        for a in hired_list[:TTH_SAMPLE]:
            logs = api(f"/accounts/{acc}/applicants/{a['id']}/logs?count=100", quiet=True)
            items = (logs or {}).get("items", [])
            if not items:
                continue
            dates = [d(x.get("created")) for x in items if d(x.get("created"))]
            hired_dates = [d(x.get("created")) for x in items
                           if x.get("status") in hired_ids and d(x.get("created"))]
            if dates and hired_dates:
                delta = (max(hired_dates) - min(dates)).days
                if 0 < delta < 730:
                    tth.append(delta)
    tth_med = median(tth)
    print(f"Time to hire: выборка {len(tth)} наймов, медиана {tth_med} дн")

    # --- нагрузка на рекрутёра ---
    cw = api(f"/accounts/{acc}/coworkers?count=100") or {}
    members = cw.get("items", [])
    types = {}
    for m in members:
        t = str(m.get("member_type") or m.get("type") or "—")
        types[t] = types.get(t, 0) + 1
    print("Команда по ролям: " + ", ".join(f"{k}:{v}" for k, v in types.items()))
    recruiters = sum(v for k, v in types.items()
                     if k.lower() in ("owner", "manager", "recruiter"))
    load = round(len(open_vac) / recruiters, 1) if recruiters else None

    # --- сборка ---
    values = {
        "hope_vacancies_open": len(open_vac),
        "hope_vacancies_overdue": len(overdue),
        "hope_funnel_active": funnel_total if funnel else None,
        "hope_time_to_hire": tth_med,
        "hope_offer_to_hire": offer_to_hire,
        "hope_nagruzka_rekrutera": load,
    }
    values = {k: v for k, v in values.items() if v is not None}

    print("Насчитано:", json.dumps(values, ensure_ascii=False))
    if not values:
        sys.exit("Нечего записывать — смотреть строки выше, где сорвалось")

    data = {"updated": today.isoformat(), "values": {}, "history": {},
            "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data.setdefault("meta", {})

    # чистим метрики прошлой версии
    data.get("values", {}).pop("hope_voronka_naima", None)

    data["updated"] = today.isoformat()
    data["values"].update(values)
    data["meta"]["hope_source"] = f"Huntflow API, снято {now.strftime('%Y-%m-%d %H:%M UTC')}"
    if funnel:
        data["meta"]["hope_funnel"] = " · ".join(f"{n}: {c}" for n, c in funnel)
    if overdue:
        data["meta"]["hope_overdue_list"] = "; ".join(str(x) for x in overdue[:10])
    if tth:
        data["meta"]["hope_tth_base"] = f"медиана по {len(tth)} последним наймам"

    for key, val in values.items():
        hist = [p for p in data["history"].get(key, []) if p["d"] != today.isoformat()]
        hist.append({"d": today.isoformat(), "v": val})
        data["history"][key] = sorted(hist, key=lambda p: p["d"])[-90:]

    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("OK: записано в data/metrics.json")


if __name__ == "__main__":
    main()

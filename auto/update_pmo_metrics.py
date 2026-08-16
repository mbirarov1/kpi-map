#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автообновление PMO-метрик для борда «Метрики по горизонталям». Версия 2.

Считает 14 метрик по доске «Проекты» (board_id=1347171):
  поток      : pmo_active, pmo_queue, pmo_done30, pmo_leadtime, pmo_cycletime
  сроки      : pmo_ontime, pmo_aging, pmo_stale, pmo_overdue
  блокировки : pmo_blocked
  готовность : pmo_checklist, pmo_due_filled, pmo_strukturnaya_celostnost, pmo_report_ok

Что НЕ считается и почему (проверено по факту данных 14.08.2026):
  - утилизация ресурсов, затраты часов — тайм-трекинг в карточках не ведётся (time_spent_sum=0 у всех);
  - бюджетные метрики (SPI/CPI, EAC) — в Kaiten нет бюджетов;
  - точность оценок — оценки (size/estimate_workload) не заполняются (0 из 32).

Запуск:  KAITEN_TOKEN=xxx python3 update_pmo_metrics.py
Токен:   Kaiten → профиль → «Ключи API». В GitHub — хранить в Secrets, в код не вшивать.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

KAITEN_HOST = "https://hq.kaiten.ru"
BOARD_ID = 1347171          # доска «Проекты» PMO
LIMIT_PORTFOLIO = 12        # п. 1.8.2 регламента
STALE_DAYS = 14             # порог свежести — п. 1.10
AGING_DAYS = 30             # порог «застрял в стадии»
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.json")

ACTIVE_STAGES = ["Инициация", "Определение стейкхолдеров", "Согласование целей",
                 "Цели и ожидания", "Планирование", "Декомпозиция", "Согласовано",
                 "Реализация проекта", "Приемка"]
QUEUE_STAGES = ["Подготовка к квалификации", "Квалификация", "На рассмотрение",
                "Зеленый свет", "Ждет освобождения лимита"]
DONE_STAGES = ["Завершен успешно", "Административно завершен"]

# Кастомные поля паспорта проекта (id → смысл)
P_GOAL, P_RP, P_KEYSTAKE = "id_1399", "id_527630", "id_568754"
P_STRAT, P_CATEGORY, P_REPORT_OK = "id_554946", "id_554948", "id_602149"


def api(path):
    token = os.environ.get("KAITEN_TOKEN")
    if not token:
        sys.exit("Нет KAITEN_TOKEN в переменных окружения")
    req = urllib.request.Request(
        KAITEN_HOST + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def d(iso):
    if not iso:
        return None
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).date()


def median(a):
    a = sorted(a)
    return a[len(a) // 2] if a else None


def main():
    now = datetime.now(timezone.utc)
    today = now.date()

    cols = api(f"/api/latest/boards/{BOARD_ID}/columns")
    col_title = {}
    for c in cols:
        col_title[c["id"]] = c["title"]
        for s in c.get("subcolumns") or []:
            # подколонку узнаём по её собственному названию, стадию — по родителю
            col_title[s["id"]] = s["title"] + "|" + c["title"]

    cards = api(f"/api/latest/cards?board_id={BOARD_ID}&limit=500")
    rows = []
    for c in cards:
        if c.get("archived"):
            continue
        col = col_title.get(c.get("column_id"), "")
        p = c.get("properties") or {}
        rows.append({
            "id": c["id"],
            "t": (c.get("title") or "")[:70],
            "o": (c.get("owner") or {}).get("full_name", "—").split()[0] if c.get("owner") else "—",
            "col": col.split("|")[-1] if "|" in col else col,
            "colfull": col,
            "created": d(c.get("created")),
            "fip": d(c.get("first_moved_to_in_progress_at")),
            "done_at": d(c.get("last_moved_to_done_at")) or d(c.get("completed_at")),
            "cca": d(c.get("column_changed_at")),
            "upd": d(c.get("updated")),
            "due": d(c.get("due_date")),
            "blocked": bool(c.get("blocked")),
            "gd": c.get("goals_done") or 0,
            "gt": c.get("goals_total") or 0,
            "goal": bool(p.get(P_GOAL)),
            "rp": bool(p.get(P_RP)),
            "ks": bool(p.get(P_KEYSTAKE)),
            "strat": bool(p.get(P_STRAT)),
            "cat": bool(p.get(P_CATEGORY)),
            "rep_ok": bool(p.get(P_REPORT_OK)),
        })

    stage = lambda r, names: any(a in r["colfull"] for a in names)
    act = [r for r in rows if stage(r, ACTIVE_STAGES)]
    queue = [r for r in rows if stage(r, QUEUE_STAGES)]
    done = [r for r in rows if stage(r, DONE_STAGES)]

    stale = [r for r in act if r["upd"] and (today - r["upd"]).days > STALE_DAYS]
    blocked = [r for r in act if r["blocked"]]
    overdue = [r for r in act if r["due"] and r["due"] < today]
    done30 = [r for r in done if r["done_at"] and (today - r["done_at"]).days <= 30]

    lt = [(r["done_at"] - r["created"]).days for r in done if r["done_at"] and r["created"]]
    ct = [(r["done_at"] - r["fip"]).days for r in done if r["done_at"] and r["fip"]]
    aging = [(today - r["cca"]).days for r in act if r["cca"]]
    stuck = [r for r in act if r["cca"] and (today - r["cca"]).days > AGING_DAYS]

    had_due = [r for r in done if r["due"]]
    on_time = [r for r in had_due if r["done_at"] and r["done_at"] <= r["due"]]

    gsum = sum(r["gt"] for r in act)
    gdone = sum(r["gd"] for r in act)
    due_filled = [r for r in act if r["due"]]
    passport = [r for r in act if r["goal"] and r["rp"] and r["ks"] and r["cat"] and r["strat"]]
    rep_ok = [r for r in done if r["rep_ok"]]

    pct = lambda a, b: round(100.0 * a / b) if b else None
    values = {
        "pmo_active": len(act),
        "pmo_queue": len(queue),
        "pmo_done30": len(done30),
        "pmo_leadtime": median(lt),
        "pmo_cycletime": median(ct),
        "pmo_ontime": pct(len(on_time), len(had_due)),
        "pmo_aging": median(aging),
        "pmo_stale": len(stale),
        "pmo_overdue": len(overdue),
        "pmo_blocked": len(blocked),
        "pmo_checklist": pct(gdone, gsum),
        "pmo_due_filled": pct(len(due_filled), len(act)),
        "pmo_strukturnaya_celostnost": pct(len(passport), len(act)),
        "pmo_report_ok": pct(len(rep_ok), len(done)),
    }

    comp_item = lambda r, dv: {"id": r["id"], "t": r["t"], "o": r["o"], "col": r["col"], "d": dv}
    comps = {
        "active": [comp_item(r, (today - r["cca"]).days if r["cca"] else None)
                   for r in sorted(act, key=lambda x: -((today - x["cca"]).days if x["cca"] else 0))],
        "stale": [comp_item(r, (today - r["upd"]).days) for r in sorted(stale, key=lambda x: -(today - x["upd"]).days)],
        "stuck": [comp_item(r, (today - r["cca"]).days) for r in sorted(stuck, key=lambda x: -(today - x["cca"]).days)],
        "blocked": [comp_item(r, None) for r in blocked],
        "overdue": [comp_item(r, (today - r["due"]).days) for r in overdue],
        "queue": [comp_item(r, (today - r["created"]).days if r["created"] else None) for r in queue],
        "nopassport": [comp_item(r, None) for r in act if r not in passport],
    }

    data = {"updated": "", "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)

    data["updated"] = today.isoformat()
    data["values"].update({k: v for k, v in values.items() if v is not None})
    data["comps"].update(comps)
    data["meta"]["pmo_source"] = f"Kaiten API, board {BOARD_ID}, снято {now.strftime('%Y-%m-%d %H:%M UTC')}"
    data["meta"]["pmo_ontime_base"] = f"{len(on_time)} из {len(had_due)} завершённых, имевших дедлайн"
    data["meta"]["pmo_stuck"] = f"{len(stuck)} из {len(act)} активных сидят в стадии > {AGING_DAYS} дн"

    for key, val in values.items():
        if val is None:
            continue
        hist = [p for p in data["history"].get(key, []) if p["d"] != today.isoformat()]
        hist.append({"d": today.isoformat(), "v": val})
        data["history"][key] = sorted(hist, key=lambda p: p["d"])[-90:]

    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("OK:", json.dumps(values, ensure_ascii=False))


if __name__ == "__main__":
    main()

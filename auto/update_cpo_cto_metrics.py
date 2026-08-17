#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автообновление метрик CPO и CTO из Kaiten. Версия 1 (17.08.2026).

CPO — из пространства «Roadmap» (доска Roadmap public, board_id=1682987):
  карточка квартала = «Q3 2026», её дети = пункты роадмапа.
    cpo_roadmap_q_gotovnost   доля пунктов текущего квартала, доехавших до проверки/оценки
    cpo_roadmap_pipeline      сколько пунктов набрано на будущие кварталы
    cpo_roadmap_ideas         сколько идей на входе (предложения + скоринг)
    cpo_roadmap_stale         пункты текущего квартала без движения дольше 30 дней

CTO — из пространства CTO (доски 1729754 «Стратегические задачи», 1729752 «Операционка»):
    cto_wip_cto               в работе у CTO + проработка + делегировано
    cto_done30_cto            закрыто за 30 дней
    cto_stale14_cto           не двигались дольше 14 дней (кроме готовых)
    cto_blocked_cto           с пометкой «заблокировано»
    cto_due_filled_cto        доля карточек с проставленным сроком
  плюс Platform (1796392, 1796522) и Системная аналитика (1281742):
    cto_platform_wip          инженерные инициативы в работе
    cto_analytics_wip         аналитика в работе

Что НЕ считается и почему (проверено 17.08.2026):
  - «успеваем ли по кварталу» — у пунктов роадмапа нет дедлайнов (0 из 35);
  - трудоёмкость и ёмкость команд — оценок нет ни в одной карточке;
  - метрики поставки (доля багов, цикл ценности) — нужны доски Dev и Service Desk, доступа нет.

Запуск:  KAITEN_TOKEN=xxx python3 update_cpo_cto_metrics.py
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

KAITEN_HOST = "https://hq.kaiten.ru"
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.json")

ROADMAP_BOARD = 1682987
CTO_BOARDS = [1729754, 1729752]
PLATFORM_BOARDS = [1796392, 1796522]
ANALYTICS_BOARD = 1281742

STALE_DAYS = 14
ROADMAP_STALE_DAYS = 30

# колонки-финалы у пунктов роадмапа
ROADMAP_DONE = ("оценка реализации цели", "проверка результатов", "готово", "релиз")
# колонки входа воронки идей
ROADMAP_IDEAS = ("предложения", "анализ", "скорринг", "скоринг", "декомпозиция от ии")
# колонки «в работе» у CTO
CTO_WIP = ("в работе", "проработка", "делегировано")
CTO_DONE = ("готово", "завершен", "завершён")


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


def cards(board_id):
    return api("/api/latest/cards?board_id=%d&archived=false&limit=500" % board_id)


def col(card):
    c = card.get("column") or {}
    return (c.get("title") or "").strip().lower()


def dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def age(iso, today):
    d = dt(iso)
    return (today - d).days if d else None


def any_of(name, variants):
    return any(v in name for v in variants)


def quarter_key(today):
    return "Q%d %d" % ((today.month - 1) // 3 + 1, today.year)


def main():
    now = datetime.now(timezone.utc)
    today = now.date()
    pct = lambda a, b: round(100.0 * a / b) if b else None
    values, meta = {}, {}

    # ---------------- CPO: роадмап ----------------
    try:
        quarters = cards(ROADMAP_BOARD)
        cur_name = quarter_key(today)
        cur = next((q for q in quarters if (q.get("title") or "").strip() == cur_name), None)

        kids = {}
        for q in quarters:
            try:
                kids[q["id"]] = api("/api/latest/cards/%d/children" % q["id"]) or []
            except Exception:
                kids[q["id"]] = []

        if cur:
            items = kids.get(cur["id"], [])
            done = [c for c in items if any_of(col(c), ROADMAP_DONE)]
            stale = [c for c in items
                     if not any_of(col(c), ROADMAP_DONE)
                     and (age(c.get("updated"), today) or 0) > ROADMAP_STALE_DAYS]
            values["cpo_roadmap_q_gotovnost"] = pct(len(done), len(items))
            values["cpo_roadmap_stale"] = len(stale)
            meta["cpo_roadmap_base"] = "%s: %d из %d пунктов доехали до проверки или оценки цели" % (
                cur_name, len(done), len(items))
        else:
            meta["cpo_roadmap_base"] = "Карточка квартала %s на доске не найдена" % cur_name

        ideas = 0
        for q in quarters:
            ideas += len([c for c in kids.get(q["id"], []) if any_of(col(c), ROADMAP_IDEAS)])

        # будущее = пункты кварталов, которые ещё не наступили
        future = 0
        for q in quarters:
            name = (q.get("title") or "").strip()
            try:
                qn, yr = int(name[1]), int(name.split()[-1])
            except (ValueError, IndexError):
                continue
            cqn, cyr = (today.month - 1) // 3 + 1, today.year
            if (yr, qn) > (cyr, cqn):
                future += len(kids.get(q["id"], []))

        values["cpo_roadmap_pipeline"] = future
        values["cpo_roadmap_ideas"] = ideas
        meta["cpo_roadmap_src"] = "Kaiten, доска Roadmap public (%d), снято %s" % (
            ROADMAP_BOARD, now.strftime("%Y-%m-%d %H:%M UTC"))
    except Exception as e:
        meta["cpo_roadmap_error"] = str(e)[:160]

    # ---------------- CTO: поток ----------------
    try:
        cto = []
        for b in CTO_BOARDS:
            cto += cards(b)
        wip = [c for c in cto if any_of(col(c), CTO_WIP)]
        done = [c for c in cto if any_of(col(c), CTO_DONE)]
        done30 = [c for c in done if (age(c.get("updated"), today) or 999) <= 30]
        stale = [c for c in cto
                 if not any_of(col(c), CTO_DONE) and (age(c.get("updated"), today) or 0) > STALE_DAYS]
        blocked = [c for c in cto if c.get("blocked")]
        due = [c for c in cto if c.get("due_date")]

        values["cto_wip_cto"] = len(wip)
        values["cto_done30_cto"] = len(done30)
        values["cto_stale14_cto"] = len(stale)
        values["cto_blocked_cto"] = len(blocked)
        values["cto_due_filled_cto"] = pct(len(due), len(cto))
        meta["cto_src"] = "Kaiten, пространство CTO (доски %s), всего %d карточек, снято %s" % (
            ", ".join(str(b) for b in CTO_BOARDS), len(cto), now.strftime("%Y-%m-%d %H:%M UTC"))
    except Exception as e:
        meta["cto_error"] = str(e)[:160]

    # ---------------- CTO: Platform и аналитика ----------------
    try:
        plat = []
        for b in PLATFORM_BOARDS:
            plat += cards(b)
        values["cto_platform_wip"] = len([c for c in plat if any_of(col(c), CTO_WIP)])
        meta["cto_platform_base"] = "%d инициатив всего, дедлайн проставлен у %d" % (
            len(plat), len([c for c in plat if c.get("due_date")]))
    except Exception as e:
        meta["cto_platform_error"] = str(e)[:160]

    try:
        an = cards(ANALYTICS_BOARD)
        values["cto_analytics_wip"] = len([c for c in an if any_of(col(c), CTO_WIP)])
        meta["cto_analytics_base"] = "%d карточек на доске «Аналитика»" % len(an)
    except Exception as e:
        meta["cto_analytics_error"] = str(e)[:160]

    # ---------------- запись ----------------
    data = {"updated": "", "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    for k in ("values", "history", "comps", "meta", "fields"):
        data.setdefault(k, {})

    data["updated"] = today.isoformat()
    data["values"].update({k: v for k, v in values.items() if v is not None})
    data["meta"].update(meta)

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

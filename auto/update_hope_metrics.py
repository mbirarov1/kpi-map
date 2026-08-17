#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE / найм — сбор данных из Huntflow. Версия 3.

Делает две вещи:
  1) data/hope.json  — детальная выгрузка для страницы «Найм»:
     вакансии, кандидаты (обезличенно: id + дата + этап + источник),
     рекрутёры (по фактическим действиям в логах), справочники статусов и источников.
  2) data/metrics.json — сводные метрики для борда:
     hope_vacancies_open, hope_vacancies_no_deadline, hope_vacancies_overdue,
     hope_time_to_hire, hope_time_to_offer, hope_nagruzka_rekrutera, hope_funnel_active

Персональные данные НЕ выгружаются: ни имён, ни телефонов, ни почт — только id кандидата.

Запуск:  HUNTFLOW_TOKEN=xxx python3 update_hope_metrics.py
Скрипт печатает диагностику по каждому шагу — первый прогон сверять глазами.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.huntflow.ru/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
METRICS_FILE = os.path.join(DATA, "metrics.json")
HOPE_FILE = os.path.join(DATA, "hope.json")

MAX_APPLICANTS_PER_VACANCY = 200   # потолок на вакансию
MAX_LOGS = 60                      # по скольким кандидатам тянуть логи (для сроков)
CLOSED_LOOKBACK_DAYS = 365         # закрытые вакансии за год — для истории

# Этапы, которые считаем «входным пулом», а не активной воронкой
POOL = ("отклик", "исходящий поиск", "просмотр заказчиком", "мессенджер", "из базы")
# Этапы вне воронки вообще
OUT = ("отказ", "резерв", "увольнение", "ис закрыт", "вышел на работу")


def api(path, quiet=False):
    token = os.environ.get("HUNTFLOW_TOKEN")
    if not token:
        sys.exit("Нет HUNTFLOW_TOKEN")
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": "kpi-board/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("401: токен истёк или отозван — обновить HUNTFLOW_TOKEN")
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
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def median(a):
    a = sorted(a)
    return a[len(a) // 2] if a else None


def paged(path_tpl, limit_pages=10):
    """Тянет постранично, path_tpl должен содержать {page}."""
    out, page = [], 1
    while page <= limit_pages:
        r = api(path_tpl.format(page=page))
        if not r:
            break
        items = r.get("items", [])
        out += items
        if len(items) < 100:
            break
        page += 1
    return out


def main():
    now = datetime.now(timezone.utc)
    today = now.date()

    acc_r = api("/accounts") or {}
    if not acc_r.get("items"):
        sys.exit("Huntflow не вернул аккаунтов")
    acc = acc_r["items"][0]["id"]
    print(f"Аккаунт: {acc_r['items'][0].get('name')} ({acc})")

    # ---------- справочники ----------
    st = api(f"/accounts/{acc}/vacancies/statuses") or {}
    statuses = [s for s in st.get("items", []) if isinstance(s.get("id"), int)]
    st_name = {s["id"]: s["name"] for s in statuses}
    st_type = {s["id"]: s.get("type") for s in statuses}
    hired_ids = [s["id"] for s in statuses if s.get("type") == "hired"]
    offer_ids = [s["id"] for s in statuses if "оффер" in s["name"].lower()]
    print(f"Статусов: {len(statuses)}; найм={hired_ids}; офферные={offer_ids}")

    src_r = api(f"/accounts/{acc}/applicants/sources") or {}
    src_name = {s["id"]: s.get("name") for s in src_r.get("items", [])}
    print(f"Источников в справочнике: {len(src_name)}")

    # ---------- вакансии ----------
    open_v = paged(f"/accounts/{acc}/vacancies?count=100&page={{page}}&state=OPEN")
    closed_v = paged(f"/accounts/{acc}/vacancies?count=100&page={{page}}&state=CLOSED")
    cutoff = now.replace(tzinfo=None) - timedelta(days=CLOSED_LOOKBACK_DAYS)
    closed_recent = [v for v in closed_v if (d(v.get("created")) or cutoff) >= cutoff]
    print(f"Вакансии: открытых {len(open_v)}, закрытых всего {len(closed_v)}, "
          f"закрытых за год {len(closed_recent)}")

    no_deadline = [v for v in open_v if not v.get("deadline")]
    overdue = []
    for v in open_v:
        dl = d(v.get("deadline"))
        if dl and dl.date() < today:
            overdue.append(v)
    print(f"Без дедлайна: {len(no_deadline)} из {len(open_v)}; просрочено: {len(overdue)}")

    # ---------- кандидаты по вакансиям ----------
    vac_rows = []
    agg = {}          # (вакансия, этап, источник, месяц) -> количество
    appl_rows = []    # временный буфер, наружу НЕ пишется
    seen_applicants = {}
    for v in open_v + closed_recent:
        vid = v["id"]
        items = paged(f"/accounts/{acc}/applicants?count=100&page={{page}}&vacancy={vid}",
                      limit_pages=max(1, MAX_APPLICANTS_PER_VACANCY // 100))
        stage_counts = {}
        for a in items:
            link = None
            for l in (a.get("links") or []):
                if l.get("vacancy") == vid:
                    link = l
                    break
            sid = (link or {}).get("status")
            sname = st_name.get(sid, "—")
            stage_counts[sname] = stage_counts.get(sname, 0) + 1
            src = src_name.get(a.get("source")) or a.get("account_source") or "не указан"
            month = str(a.get("created"))[:7]
            key = (vid, sname, str(st_type.get(sid)), str(src), month)
            agg[key] = agg.get(key, 0) + 1
            appl_rows.append({"vac": vid, "stage": sname,
                              "stage_type": st_type.get(sid), "src": src})
            seen_applicants.setdefault(a["id"], sid)
        vac_rows.append({
            "id": vid,
            "position": v.get("position"),
            "division": ((v.get("custom_name_data") or {}).get("account_division") or [None])[0],
            "state": v.get("state"),
            "created": str(v.get("created"))[:10],
            "deadline": str(v.get("deadline"))[:10] if v.get("deadline") else None,
            "to_hire": v.get("applicants_to_hire"),
            "applicants": len(items),
            "stages": stage_counts,
        })
    print(f"Кандидатов собрано: {len(appl_rows)} по {len(vac_rows)} вакансиям")

    # ---------- сроки и рекрутёры (по логам) ----------
    targets = [aid for aid, sid in seen_applicants.items()
               if sid in hired_ids or sid in offer_ids][:MAX_LOGS]
    tth, tto, recruiters = [], [], {}
    for aid in targets:
        logs = api(f"/accounts/{acc}/applicants/{aid}/logs?count=100", quiet=True)
        items = (logs or {}).get("items", [])
        if not items:
            continue
        dates = [d(x.get("created")) for x in items if d(x.get("created"))]
        if not dates:
            continue
        start = min(dates)
        for x in items:
            who = (x.get("account_info") or {}).get("name") or x.get("account")
            if who:
                recruiters[str(who)] = recruiters.get(str(who), 0) + 1
        h = [d(x.get("created")) for x in items if x.get("status") in hired_ids and d(x.get("created"))]
        o = [d(x.get("created")) for x in items if x.get("status") in offer_ids and d(x.get("created"))]
        if h:
            n = (max(h) - start).days
            if 0 < n < 730:
                tth.append(n)
        if o:
            n = (min(o) - start).days
            if 0 < n < 730:
                tto.append(n)
    print(f"Time to hire: {len(tth)} набл., медиана {median(tth)}; "
          f"Time to offer: {len(tto)} набл., медиана {median(tto)}")
    print("Активность в логах по людям: " + ", ".join(
        f"{k}:{v}" for k, v in sorted(recruiters.items(), key=lambda x: -x[1])[:8]))

    # ---------- воронка ----------
    funnel = {}
    for a in appl_rows:
        if a["stage_type"] in ("hired", "trash"):
            continue
        funnel[a["stage"]] = funnel.get(a["stage"], 0) + 1
    active = sum(c for n, c in funnel.items()
                 if not any(p in n.lower() for p in POOL)
                 and not any(o in n.lower() for o in OUT))
    pool = sum(c for n, c in funnel.items() if any(p in n.lower() for p in POOL))
    print(f"Воронка: пул {pool}, активная стадия {active}")

    # ---------- источники ----------
    sources = {}
    for a in appl_rows:
        s = sources.setdefault(a["src"], {"total": 0, "hired": 0})
        s["total"] += 1
        if a["stage_type"] == "hired":
            s["hired"] += 1

    # ---------- запись hope.json ----------
    hope = {
        "generated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "vacancies": vac_rows,
        # агрегаты вместо списка людей: ни id, ни имён — репозиторий публичный
        "agg": [{"vac": k[0], "stage": k[1], "type": k[2], "src": k[3], "m": k[4], "n": v}
                for k, v in sorted(agg.items(), key=lambda x: -x[1])],
        "funnel": funnel,
        "recruiter_activity": recruiters,
        "tth": tth, "tto": tto,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(HOPE_FILE, "w", encoding="utf-8") as f:
        json.dump(hope, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK: data/hope.json — {os.path.getsize(HOPE_FILE)//1024} КБ")

    # ---------- сводные метрики ----------
    n_rec = len([k for k, v in recruiters.items() if v >= 5]) or None
    values = {
        "hope_vacancies_open": len(open_v),
        "hope_vacancies_no_deadline": len(no_deadline),
        "hope_vacancies_overdue": len(overdue),
        "hope_time_to_hire": median(tth),
        "hope_time_to_offer": median(tto),
        "hope_funnel_active": active or None,
        "hope_nagruzka_rekrutera": round(len(open_v) / n_rec, 1) if n_rec else None,
    }
    values = {k: v for k, v in values.items() if v is not None}
    print("Насчитано:", json.dumps(values, ensure_ascii=False))

    data = {"updated": today.isoformat(), "values": {}, "history": {},
            "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(METRICS_FILE):
        with open(METRICS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    for old in ("hope_voronka_naima", "hope_offer_to_hire", "hope_cost_per_hire"):
        data.get("values", {}).pop(old, None)
    data["updated"] = today.isoformat()
    data["values"].update(values)
    data.setdefault("meta", {})["hope_source"] = f"Huntflow API, снято {now.strftime('%Y-%m-%d %H:%M UTC')}"
    data["meta"]["hope_funnel"] = " · ".join(f"{n}: {c}" for n, c in
                                             sorted(funnel.items(), key=lambda x: -x[1])[:12])
    if no_deadline:
        data["meta"]["hope_no_deadline_list"] = "; ".join(
            str(v.get("position")) for v in no_deadline[:10])
    for k, val in values.items():
        hist = [p for p in data["history"].get(k, []) if p["d"] != today.isoformat()]
        hist.append({"d": today.isoformat(), "v": val})
        data["history"][k] = sorted(hist, key=lambda p: p["d"])[-90:]
    with open(METRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("OK: записано в data/metrics.json")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Коннектор «таблица-источник → борд» для любых горизонталей.
Читает лист «Метрики» (формат Шаблона_источника_метрик) из Google Sheets
(gviz CSV, доступ «по ссылке — читатель») и обновляет data/metrics.json:
  values[key] ← «Значение» (число); history[key] ← точка на дату;
  fields[key] ← план/пульс/владелец/источник/ед.
Сопоставление строк: auto/metric_keys.json («Направление|Метрика» → key).

Запуск:  SOURCE_SHEET_ID=<id> SOURCE_GID=<gid> python3 update_source_sheet.py
Тест:    SOURCE_CSV_FILE=mock.csv python3 update_source_sheet.py
"""

import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "..", "data", "metrics.json")
KEYS_FILE = os.path.join(HERE, "metric_keys.json")

HEAD = ["Направление", "Метрика", "Значение", "Ед.", "Дата значения",
        "План", "Пульс", "Ответственный", "Источник данных", "Комментарий"]


def read_csv_text():
    path = os.environ.get("SOURCE_CSV_FILE")
    if path:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    sheet = os.environ.get("SOURCE_SHEET_ID")
    gid = os.environ.get("SOURCE_GID", "0")
    if not sheet:
        sys.exit("Нет SOURCE_SHEET_ID (или SOURCE_CSV_FILE для теста)")
    url = (f"https://docs.google.com/spreadsheets/d/{sheet}/gviz/tq"
           f"?tqx=out:csv&gid={gid}")
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def parse_number(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace(" ", " ")
    s = re.sub(r"[%₽]", "", s).strip()
    s = s.replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return None


def parse_date(raw, default):
    s = str(raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return default


def main():
    today = datetime.now(timezone.utc).date().isoformat()

    with open(KEYS_FILE, encoding="utf-8") as f:
        keys = json.load(f)

    rows = list(csv.reader(io.StringIO(read_csv_text())))
    if not rows:
        sys.exit("Лист пуст")
    head = [h.strip() for h in rows[0]]
    if head[:2] != HEAD[:2]:
        sys.exit(f"Заголовки не совпадают с шаблоном: {head[:3]}")
    col = {name: head.index(name) for name in HEAD if name in head}

    data = {"updated": today, "values": {}, "history": {},
            "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data.setdefault("fields", {})

    updated, skipped, unknown = 0, 0, []
    for r in rows[1:]:
        def cell(name):
            i = col.get(name)
            return r[i].strip() if i is not None and i < len(r) else ""

        h, name = cell("Направление"), cell("Метрика")
        if not h or not name:
            continue
        key = keys.get(f"{h}|{name}")
        if not key:
            unknown.append(f"{h}|{name}")
            continue

        val = parse_number(cell("Значение"))
        if val is not None:
            data["values"][key] = val
            d = parse_date(cell("Дата значения"), today)
            hist = [p for p in data["history"].get(key, []) if p["d"] != d]
            hist.append({"d": d, "v": val})
            data["history"][key] = sorted(hist, key=lambda p: p["d"])[-90:]
            updated += 1
        else:
            skipped += 1

        f = {}
        for src, dst in [("План", "plan"), ("Пульс", "pulse"),
                         ("Ответственный", "owner"),
                         ("Источник данных", "source"), ("Ед.", "unit")]:
            if cell(src):
                f[dst] = cell(src)
        if f:
            plan_num = parse_number(f.get("plan", ""))
            if plan_num is not None:
                f["planNum"] = plan_num
            data["fields"].setdefault(key, {}).update(f)

    data["updated"] = today
    data["meta"]["sheet_source"] = (
        f"таблица-источник, снято {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}: "
        f"{updated} значений обновлено, {skipped} строк без числа")
    if unknown:
        data["meta"]["sheet_unknown"] = ("строки без пары на борде: " + "; ".join(unknown[:10]))

    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("OK:", json.dumps({"updated": updated, "skipped": skipped,
                             "unknown": len(unknown)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

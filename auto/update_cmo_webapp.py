#!/usr/bin/env python3
"""CMO: закрытые таблицы (сводная Матевосяна + когортная) через веб-приложение
Apps Script Бирарова. URL в секрете CMO_WEBAPP_URL. Пишет values+history
в data/metrics.json. Колонки — как в живом чтении борда:
сводная: строки после «2026 год», col2 — номер/число недели, col14 — CR-REG %,
col18 — регистрации CDX; когортная: строка «2026 год…», col27 LTV/CAC,
col28 CPL, col29 CAC.
"""
import json, os, sys, urllib.request, datetime

URL = os.environ.get("CMO_WEBAPP_URL")
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.json")

def num(x):
    if x is None: return None
    s = str(x).strip().replace(" ", "").replace(" ", "").replace("%", "").replace(",", ".")
    if not s or s in ("-", "—"): return None
    try: return float(s)
    except ValueError: return None

def main():
    if not URL:
        print("CMO_WEBAPP_URL не задан — пропускаю")
        return
    req = urllib.request.Request(URL, headers={"User-Agent": "kpi-map-bot"})
    with urllib.request.urlopen(req, timeout=90) as r:
        j = json.load(r)
    today = datetime.date.today().isoformat()
    year = datetime.date.today().year
    values, history, fields = {}, {}, {}

    sv = j.get("svodnaya") or []
    if sv:
        y = next((i for i, r in enumerate(sv) if "2026 год" in str(r[0] if r else "")), -1)
        cr_hist, cdx_hist = [], []
        if y >= 0:
            # понедельные точки: неделя k -> дата = первый понедельник 2026 + 7*(k-1)
            base = datetime.date(2026, 1, 5)
            wk = 0
            for r in sv[y + 1:]:
                if not r or num(r[2] if len(r) > 2 else None) is None: continue
                wk += 1
                d = (base + datetime.timedelta(days=7 * (wk - 1))).isoformat()
                cr = num(r[14] if len(r) > 14 else None)
                if cr is not None and cr <= 1:
                    cr = cr * 100  # raw getValues отдаёт доли (0.0712), CSV отдавал проценты
                cd = num(r[18] if len(r) > 18 else None)
                if cr is not None: cr_hist.append({"d": d, "v": round(cr, 2)})
                if cd is not None: cdx_hist.append({"d": d, "v": cd})
        if cr_hist:
            values["cmo_cr_reg_konversiya_v_registraciyu"] = str(cr_hist[-1]["v"]).replace(".", ",") + " %"
            history["cmo_cr_reg_konversiya_v_registraciyu"] = cr_hist
        if cdx_hist:
            values["cmo_registracii_cdx"] = int(cdx_hist[-1]["v"])
            history["cmo_registracii_cdx"] = cdx_hist
        print(f"сводная: CR-REG точек {len(cr_hist)}, CDX точек {len(cdx_hist)}")
    else:
        print("сводная: " + str(j.get("svodnaya_error"))[:120])

    coh = j.get("cohort") or []
    if coh:
        row = next((r for r in coh if str((r[1] if r and len(r) > 1 else "")).strip().startswith("2026 год")), None)
        if row:
            lc, cpl, cac = num(row[27] if len(row) > 27 else None), num(row[28] if len(row) > 28 else None), num(row[29] if len(row) > 29 else None)
            if lc is not None: values["cmo_ltv_cac"] = str(round(lc, 1)).replace(".", ",")
            if cpl is not None: values["cmo_cpl_stoimost_lida"] = f"{round(cpl)} ₽"
            if cac is not None: values["cmo_cac"] = f"{round(cac):,} ₽".replace(",", " ")
            print(f"когортная 2026: LTV/CAC {lc}, CPL {cpl}, CAC {cac}")
        else:
            print("когортная: строка 2026 не найдена")
    else:
        print("когортная: " + str(j.get("cohort_error"))[:120])

    data = {"updated": "", "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data["updated"] = today
    data["values"].update(values)
    data.setdefault("history", {}).update(history)
    data.setdefault("meta", {})["cmo_webapp_source"] = (
        "Веб-приложение (сводная+когортная), снято %s UTC" % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("OK: %d значений, %d рядов истории" % (len(values), len(history)))

if __name__ == "__main__":
    main()

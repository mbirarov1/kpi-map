#!/usr/bin/env python3
"""CMO: закрытые таблицы (сводная Матевосяна + когортная) через веб-приложение
Apps Script Бирарова. URL в секрете CMO_WEBAPP_URL. Пишет values+history
в data/metrics.json. Колонки — как в живом чтении борда:
сводная: строки после «2026 год», col2 — номер/число недели, col14 — CR-REG %,
col18 — регистрации CDX; когортная: строка «2026 год…», col27 LTV/CAC,
col28 CPL, col29 CAC.
"""
import json, os, sys, time, urllib.request, datetime

def fetch_json(url, tries=3, pause=25):
    """Apps Script иногда транзиентно отдаёт 404/5xx — ретраим."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kpi-map-bot"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as ex:
            last = ex
            print("fetch попытка %d не прошла: %s" % (i + 1, str(ex)[:100]))
            if i < tries - 1:
                time.sleep(pause)
    raise last

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
    j = fetch_json(URL)
    today = datetime.date.today().isoformat()
    year = datetime.date.today().year
    values, history, fields = {}, {}, {}

    sv = j.get("svodnaya") or []
    if sv:
        y = next((i for i, r in enumerate(sv) if "2026 год" in str(r[0] if r else "")), -1)
        cr_hist, cdx_hist, trf_hist = [], [], []
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
                tv2 = num(r[2])
                if tv2 is not None: trf_hist.append({"d": d, "v": int(tv2)})
        if cr_hist:
            values["cmo_cr_reg_konversiya_v_registraciyu"] = str(cr_hist[-1]["v"]).replace(".", ",") + " %"
            history["cmo_cr_reg_konversiya_v_registraciyu"] = cr_hist
        if cdx_hist:
            values["cmo_registracii_cdx"] = int(cdx_hist[-1]["v"])
            history["cmo_registracii_cdx"] = cdx_hist
        if trf_hist:
            values["cmo_trafik"] = trf_hist[-1]["v"]
            history["cmo_trafik"] = trf_hist
        print(f"сводная: CR-REG точек {len(cr_hist)}, CDX {len(cdx_hist)}, трафик {len(trf_hist)}")
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

    # Трек метрик (18zR…): листы MRR A+/B/CDX, последняя точка замера в строках ставок.
    # В таблице доли (0.0278) и местами уже проценты (3.16) — правило: v<=1 -> x100.
    TREK = [("A+", "1596315827"), ("B", "2086128744"), ("CDX", "1473929562")]
    NEWKEY = {"A+": "cmo_new_mrr_aplus", "CDX": "cmo_new_mrr_cdx"}
    ROWS = {"MRR Growth rate": "seg_mrr_growth_rate",
            "NEW MRR growth rate": "seg_new_mrr_growth_rate",
            "MRR Expansion rate": "seg_mrr_expansion_rate",
            "MRR Contraction Rate": "seg_mrr_contraction_rate",
            "MRR Churn Rate": "seg_mrr_churn_rate"}
    seg_parts = {k: [] for k in ROWS.values()}
    sep = "&" if "?" in URL else "?"
    trek_ok = 0
    for seg, gid in TREK:
        try:
            tv = fetch_json(URL + sep + "src=trek&gid=" + gid).get("values") or []
        except Exception as ex:
            print("трек %s: ошибка %s" % (seg, str(ex)[:80]))
            continue
        trek_ok += 1
        for row in tv:
            name = str(row[0] if row else "").strip()
            if name == "New" and seg in NEWKEY:
                lastn = None
                for cell in row[1:]:
                    if isinstance(cell, (int, float)):
                        lastn = float(cell)
                if lastn is not None:
                    values[NEWKEY[seg]] = "{:,.0f}".format(round(lastn)).replace(",", " ") + " ₽"
                    if seg == "CDX":
                        values["_new_cdx_raw"] = lastn
                continue
            if name not in ROWS:
                continue
            last = None
            for cell in row[1:]:
                if isinstance(cell, (int, float)):
                    last = float(cell)
            if last is None:
                continue
            pct = last * 100 if abs(last) <= 1 else last
            seg_parts[ROWS[name]].append("%s %s %%" % (seg, str(round(pct * 10) / 10).replace(".", ",")))
    for key, parts in seg_parts.items():
        if parts:
            values[key] = " · ".join(parts)
    if seg_parts.get("seg_mrr_churn_rate"):
        # ревизия CS 26.08: churn по сегментам — рабочий инструмент CS, дублируем композит
        values["cs_churn_segments"] = " · ".join(seg_parts["seg_mrr_churn_rate"])
    # мост CDX: New MRR CDX последнего месяца / регистрации за ~месяц (4 недели)
    ncdx = values.pop("_new_cdx_raw", None)
    regs4 = sum(p["v"] for p in cdx_hist[-4:]) if sv and cdx_hist else 0
    if ncdx and regs4:
        values["cmo_rub_per_reg_cdx"] = str(round(ncdx / regs4)) + " ₽"
        print("мост CDX: %.0f / %.0f = %s" % (ncdx, regs4, values["cmo_rub_per_reg_cdx"]))
    if trek_ok:
        print("трек: %d листов, метрик собрано %d" % (trek_ok, sum(1 for p in seg_parts.values() if p)))

    data = {"updated": "", "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data["updated"] = today
    # ревизия CMO 26.08: удалённые с борда метрики чистим из данных
    for dead in ("cmo_mrr_per_rub", "cmo_payback", "cmo_cac_b", "cmo_mql_cdx",
                 "cmo_sql_a_plus", "cmo_cr_mql_sql_a_plus", "cs_nrr_grr", "cs_churn_pre_churn"):
        data.get("values", {}).pop(dead, None)
        data.get("history", {}).pop(dead, None)
    data["values"].update(values)
    data.setdefault("history", {}).update(history)
    data.setdefault("meta", {})["cmo_webapp_source"] = (
        "Веб-приложение (сводная+когортная), снято %s UTC" % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    if trek_ok:
        data["meta"]["seg_trek_source"] = (
            "Трек метрик, листы MRR A+/B/CDX (веб-приложение), снято %s UTC"
            % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("OK: %d значений, %d рядов истории" % (len(values), len(history)))

if __name__ == "__main__":
    main()

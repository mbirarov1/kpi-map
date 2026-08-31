#!/usr/bin/env python3
"""KPI goals B (Google Sheets 155J7…) через веб-приложение Бирарова (?src=kpib&gid=0).
Порт карты метрик с борда (srcKpiGoals): [лидер, строка метрики, ключ, вид].
Вид: 'n' число, 'p' проценты (в таблице доли -> x100), 'r' рубли.
Пишет values + помесячную history в data/metrics.json.
"""
import json, os, time, urllib.request, datetime

def fetch_json(url, tries=3, pause=25):
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

MAP = [
    ("Халезов", "Segment MRR", "seg_b_mrr", "r"),
    ("Селиванов", "NPS продукта", "cpo_nps", "n"),
    ("Селиванов", "Roadmap Completion", "cpo_roadmap_completion", "p"),
    ("Лебедева", "MQL/мес", "cmo_mql_plan_fakt", "n"),
    ("Лебедева", "SQL/мес", "cmo_sql", "n"),
    ("Лебедева", "CR MQL→SQL", "cmo_cr_mql_sql", "p"),
    ("Лебедева", "New MRR", "cmo_new_mrr", "r"),
    ("Пругер", "Net Growth", "seg_b_net_growth_mes", "r"),
    ("Пругер", "MRR Rate", "seg_b_mrr_rate", "p"),
    ("Былинкин", "Win Rate", "seg_b_win_rate", "p"),
    ("Былинкин", "ARPA", "seg_b_arpa", "r"),
    ("Шеповалов", "NRR", "cs_nrr", "p"),
    ("Шеповалов", "GRR", "cs_grr", "p"),
    ("Шеповалов", "Churned MRR", "cs_churned_mrr", "r"),
    ("Шеповалов", "Contruction MRR", "cs_contraction_mrr", "r"),
    ("Шеповалов", "Expansion MRR", "cs_expansion_mrr", "r"),
    ("Шеповалов", "Churn Rate", "cs_churn_rate", "p"),
    ("Шеповалов", "Health Score avg", "cs_health_score", "n"),
    ("Павел", "Partner Revenue", "part_partner_revenue_vyruchka_kanala", "r"),
    ("Павел", "Partner New MRR", "part_partner_new_mrr", "r"),
    ("Кирюхин", "CSAT", "sup_csat", "n"),
]
MONTH = {"Январь": "01", "Февраль": "02", "Март": "03", "Апрель": "04", "Май": "05",
         "Июнь": "06", "Июль": "07", "Август": "08", "Сентябрь": "09",
         "Октябрь": "10", "Ноябрь": "11", "Декабрь": "12"}
EOM = {"01": "31", "02": "28", "03": "31", "04": "30", "05": "31", "06": "30",
       "07": "31", "08": "31", "09": "30", "10": "31", "11": "30", "12": "31"}


def cell_pct(x):
    """Ячейка процентной строки: число = доля (x100), строка с % = уже проценты."""
    if isinstance(x, (int, float)):
        return float(x) * 100.0
    s = str(x).strip().replace(",", ".")
    if s.endswith("%"):
        try:
            return float(s[:-1].strip())
        except ValueError:
            return None
    try:
        return float(s) * 100.0
    except ValueError:
        return None


def cell_num(x):
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".")
    if not s or s in ("-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v, kind):
    if kind == "r":
        return "{:,.0f}".format(round(v)).replace(",", " ") + " ₽"
    if kind == "p":
        return str(round(v * 100) / 100).replace(".", ",").rstrip("0").rstrip(",") + " %"
    r = round(v * 100) / 100
    return int(r) if r == int(r) else r


def main():
    if not URL:
        print("CMO_WEBAPP_URL не задан — пропускаю")
        return
    sep = "&" if "?" in URL else "?"
    j = fetch_json(URL + sep + "src=kpib&gid=0")
    rows = j.get("values") or []
    if not rows:
        print("kpib: пусто — " + str(j.get("error"))[:120])
        return
    head = rows[0]
    fc = []  # (col_index, 'MM')
    for i, h in enumerate(head):
        t = str(h or "").strip()
        for mn, mm in MONTH.items():
            if t.startswith(mn):
                fc.append((i, mm))
                break
    values, history = {}, {}
    lead = ""
    year = 2026
    for r in rows[1:]:
        if r and str(r[0] or "").strip():
            lead = str(r[0]).strip()
        met = str(r[2] if len(r) > 2 else "").strip()
        if not met:
            continue
        for ml, mm_name, key, kind in MAP:
            if not lead.startswith(ml) or met != mm_name:
                continue
            pts, last = [], None
            for ci, mm in fc:
                raw = r[ci] if len(r) > ci else ""
                if raw == "" or raw is None:
                    continue
                v = cell_pct(raw) if kind == "p" else cell_num(raw)
                if v is None:
                    continue
                d = "%d-%s-%s" % (year, mm, EOM[mm])
                pv = round(v * 100) / 100
                pts.append({"d": d, "v": pv})
                last = v
            if last is None:
                continue
            values[key] = fmt(last, kind)
            history[key] = pts
    # мост B: New MRR B на один SQL — суммы за последние 3 факт-месяца (ревизия 26.08)
    nm = history.get("cmo_new_mrr") or []
    sq = history.get("cmo_sql") or []
    if nm and sq:
        months = sorted(set(p["d"] for p in nm) & set(p["d"] for p in sq))[-3:]
        s_nm = sum(p["v"] for p in nm if p["d"] in months)
        s_sq = sum(p["v"] for p in sq if p["d"] in months)
        if s_sq:
            values["cmo_rub_per_sql_b"] = "{:,.0f}".format(round(s_nm / s_sq)).replace(",", " ") + " ₽"
            print("мост B: %.0f / %.0f (мес: %s) = %s" % (s_nm, s_sq, ",".join(m[5:7] for m in months), values["cmo_rub_per_sql_b"]))

    # защита обновляемости: пинг, если последний факт-месяц MQL B протух (ревизия 26.08)
    kpib_warning = None
    mql = history.get("cmo_mql_plan_fakt") or []
    if mql:
        last_d = datetime.date.fromisoformat(mql[-1]["d"])
        age = (datetime.date.today() - last_d).days
        if age > 50:
            kpib_warning = ("KPI goals B: последний факт-месяц %s, прошло %d дн — "
                            "данные Полетаева протухли (норма: раз в 2 недели)" % (mql[-1]["d"][:7], age))
            print("ПИНГ: " + kpib_warning)

    data = {"updated": "", "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data["updated"] = datetime.date.today().isoformat()
    data["values"].update(values)
    data.setdefault("history", {}).update(history)
    data.setdefault("meta", {})["kpib_source"] = (
        "KPI goals B (веб-приложение), снято %s UTC" % datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    if kpib_warning:
        data["meta"]["kpib_warning"] = kpib_warning
    else:
        data["meta"].pop("kpib_warning", None)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("kpib OK: %d значений, %d рядов истории" % (len(values), len(history)))
    for k in sorted(values):
        print("  %s = %s" % (k, values[k]))


if __name__ == "__main__":
    main()

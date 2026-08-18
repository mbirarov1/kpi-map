#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Метрики из биллинга billed.me → data/metrics.json

Что считает и почему именно так:

  ПРИЗНАННАЯ ВЫРУЧКА (recognized revenue)
    Оплаченный счёт нельзя брать как выручку месяца: 92 % денег приходит
    годовыми и более длинными предоплатами. Поэтому каждый счёт размазывается
    равномерно по своему периоду подписки (period_started_at → period_ended_at),
    и в месяц засчитывается только та доля, которая на него приходится.
    Проверено на июле 2026: метод даёт 45,8 млн ₽ против 46,3 млн MRR — расхождение 1,1 %.

  ПВМУ — признанная выручка минус услуги
    Термин Славы. В биллинге услуги отделяются полем invoice_type=one_time_service.
    ВАЖНО: в июле 2026 услуг в биллинге всего 130 300 ₽ из 530 млн (0,02 %).
    Если по факту услуг больше — значит они идут мимо биллинга, и ПВМУ,
    посчитанный здесь, завышен. Это надо подтвердить у финансов.

  КЭШ (оплачено за месяц)
    Сумма оплаченных счетов. Не выручка. Нужна для кассового разрыва и для того,
    чтобы было видно разницу между «пришло денег» и «заработали».

Доступ: нужен токен биллинга в секрете BILLING_TOKEN.
На 18.08.2026 раздел /account/api/api-keys отдаёт 403 — ключ надо запросить
у владельца биллинга. Без токена скрипт молча выходит, ничего не ломая.
"""
import os, json, sys, urllib.request, urllib.error
from datetime import date, datetime, timedelta
from calendar import monthrange

BASE     = os.environ.get("BILLING_BASE", "https://billed.me")
TOKEN    = os.environ.get("BILLING_TOKEN", "").strip()
CURRENCY = os.environ.get("BILLING_CURRENCY", "RUB")
MONTHS   = int(os.environ.get("BILLING_MONTHS", "6"))
OUT      = "data/metrics.json"


def api(path):
    req = urllib.request.Request(BASE + path, headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + TOKEN,
        "User-Agent": "kpi-map-bot",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  ! {path.split('?')[0]} → HTTP {e.code}")
    except Exception as e:
        print(f"  ! {path.split('?')[0]} → {e}")
    return None


def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def month_bounds(y, m):
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def invoices(d_from, d_to):
    """Оплаченные счета за период. Пагинации у эндпоинта нет — берём большим лимитом."""
    j = api(f"/account/api/invoices?currency={CURRENCY}&status=paid&limit=20000"
            f"&date_from={d_from}&date_to={d_to}")
    return (j or {}).get("invoices", [])


def recognized(inv, m_start, m_end):
    """Доля каждого счёта, приходящаяся на месяц [m_start, m_end]."""
    total = 0.0
    for x in inv:
        amount = float(x.get("total_amount") or x.get("amount") or 0)
        s, e = parse_dt(x.get("period_started_at")), parse_dt(x.get("period_ended_at"))
        if not s or not e or e <= s:
            total += amount          # без периода — считаем разовым в этом месяце
            continue
        days = max(1, (e - s).days)
        ov_s = max(s.date(), m_start)
        ov_e = min(e.date(), m_end)
        if ov_e >= ov_s:
            total += amount * ((ov_e - ov_s).days + 1) / days
    return total


def fmt(v, unit="₽"):
    v = round(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.1f}".replace(".", ",") + f" млн {unit}"
    return f"{v:,}".replace(",", " ") + f" {unit}"


def main():
    if not TOKEN:
        print("BILLING_TOKEN не задан — пропускаю биллинг (это не ошибка).")
        return 0

    today = date.today()
    # последний полный месяц
    first_this = today.replace(day=1)
    last_full_end = first_this - timedelta(days=1)
    y, mo = last_full_end.year, last_full_end.month

    hist_rec, hist_pvmu, hist_srv, hist_cash = [], [], [], []
    cur = None

    for i in range(MONTHS):
        yy, mm = y, mo - i
        while mm <= 0:
            mm += 12
            yy -= 1
        m_start, m_end = month_bounds(yy, mm)
        # берём счета с запасом назад: длинные подписки оплачены раньше
        d_from = (m_start - timedelta(days=1100)).isoformat()
        inv = invoices(d_from, m_end.isoformat())
        if not inv:
            print(f"  {yy}-{mm:02d}: счетов нет")
            continue

        rec = recognized(inv, m_start, m_end)
        srv_inv = [x for x in inv if x.get("invoice_type") == "one_time_service"]
        srv = recognized(srv_inv, m_start, m_end)
        cash = sum(float(x.get("total_amount") or x.get("amount") or 0)
                   for x in inv
                   if (parse_dt(x.get("paid_date")) or parse_dt(x.get("created_at")) or datetime(1970,1,1)).date().replace(day=1) == m_start)
        pt = m_end.isoformat()
        hist_rec.append({"d": pt, "v": round(rec)})
        hist_pvmu.append({"d": pt, "v": round(rec - srv)})
        hist_srv.append({"d": pt, "v": round(srv)})
        hist_cash.append({"d": pt, "v": round(cash)})
        print(f"  {yy}-{mm:02d}: признанная {fmt(rec)} · услуги {fmt(srv)} · ПВМУ {fmt(rec-srv)} · оплачено {fmt(cash)}")
        if i == 0:
            cur = (rec, srv, cash)

    if cur is None:
        print("Не удалось посчитать ни одного месяца.")
        return 1

    rec, srv, cash = cur
    values = {
        "fin_recognized_revenue": fmt(rec),
        "fin_pvmu":               fmt(rec - srv),
        "fin_services_revenue":   fmt(srv),
        "fin_cash_collected":     fmt(cash),
        "fin_services_share":     (f"{srv/rec*100:.2f}".replace(".", ",") + " %") if rec else None,
    }

    # AI usage — побочная польза биллинга
    ai = api("/account/api/ai-usage/summary")
    if ai:
        tok = (ai.get("total_llm_amount") or 0) + (ai.get("total_embedder_amount") or 0)
        cost = (ai.get("total_llm_cost") or 0) + (ai.get("total_embedder_cost") or 0)
        if tok:
            values["ai_tokens_30d"] = f"{int(tok):,}".replace(",", " ")
        if cost:
            values["ai_cost_30d"] = fmt(cost)

    values = {k: v for k, v in values.items() if v is not None}
    print("Насчитано:", json.dumps(values, ensure_ascii=False))

    data = {"updated": today.isoformat(), "values": {}, "history": {}, "comps": {}, "meta": {}, "fields": {}}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    data["updated"] = today.isoformat()
    data.setdefault("values", {}).update(values)
    data.setdefault("history", {})
    for k, h in (("fin_recognized_revenue", hist_rec), ("fin_pvmu", hist_pvmu),
                 ("fin_services_revenue", hist_srv), ("fin_cash_collected", hist_cash)):
        if h:
            data["history"][k] = sorted(h, key=lambda p: p["d"])
    data.setdefault("meta", {})["billing_source"] = (
        f"billed.me, снято {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}; "
        f"признанная выручка = счета, размазанные по периоду подписки")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"OK: записано в {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

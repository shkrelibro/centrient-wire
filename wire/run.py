"""Centrient wire orchestrator v2. Every run: news sweep (rotation-aware) + regulator watchers.
05:xx UTC (or FORCE_DATA): daily data leg (ChemicalBook, PharmaCompass, FX, DCE, bond quotes);
Mondays add the Eastmoney broker-PDF prints; from the 25th the Comtrade/Comext trade mirror.
Exports docs/centrient_news.json and docs/centrient_data.json. Per-feed failure is a visible
status, never a crashed run; manual-only sources are listed, not forgotten."""
import csv, datetime as dt, json, os, sys
from pathlib import Path
import httpx, yaml

sys.path.insert(0, str(Path(__file__).parent))
import feeds, news, watchers

ROOT = Path(__file__).resolve().parents[1]
DATA, DOCS = ROOT / "data", ROOT / "docs"
PDFS, HIST = DATA / "pdfs", DATA / "history.csv"
SEEN, NEWSSTORE, WSTATE = DATA / "seen.json", DATA / "news_window.json", DATA / "watch_state.json"

THRESHOLDS = {
    "6apa_rmb_kg": {"cash_floor": [135, 145], "war_below": 180, "ok_above": 200, "mip_ceiling_ref": 260,
                    "note": "broker/Wind basis RMB/kg; ChemicalBook is RMB/t list quotes and reads high — divide by 1000, direction only"},
    "peng_usd_kg": {"crash_ref": 13.5, "first_tell": 18, "strike_band": [22, 28], "india_mip": 26.5,
                    "note": "strike band computed (2.0-2.5 kg Pen G per kg 7-ADCA), not published"},
    "bond": {"rule": "10pts implied restructuring probability ~ 4.5-5 bond pts; re-underwrite on -5pts"},
    "calendar": {"mip_expiry": "2027-01-28", "contract_reset": "January"},
}

def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def append_history(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    new = not HIST.exists()
    with open(HIST, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["series", "date", "value", "unit", "source", "snippet", "fetched_at"])
        if new:
            w.writeheader()
        ts = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        for r in rows:
            w.writerow({**{k: r.get(k, "") for k in ["series", "date", "value", "unit", "source", "snippet"]},
                        "fetched_at": ts})

def series_map():
    out = {}
    if not HIST.exists():
        return out
    with open(HIST, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["value"] in ("", "None"):
                continue
            out.setdefault(r["series"], []).append(r)
    m = {}
    for name, s in out.items():
        s.sort(key=lambda x: (x["date"], x["fetched_at"]))
        latest, prev = s[-1], (s[-2] if len(s) > 1 else None)
        try:
            delta = round(100 * (float(latest["value"]) / float(prev["value"]) - 1), 1) if prev else None
        except Exception:
            delta = None
        m[name] = {"latest": {"date": latest["date"], "value": float(latest["value"]), "unit": latest["unit"]},
                   "prev": ({"date": prev["date"], "value": float(prev["value"])} if prev else None),
                   "delta_pct": delta, "source": latest["source"],
                   "history_tail": [{"date": x["date"], "value": float(x["value"])} for x in s[-10:]]}
    return m

def main():
    now = dt.datetime.utcnow()
    universe = yaml.safe_load((ROOT / "config" / "universe.yaml").read_text(encoding="utf-8"))
    force = lambda k: os.environ.get(k, "").lower() == "true"
    do_data = now.hour == 5 or force("FORCE_DATA") or force("FORCE_WEEKLY") or force("FORCE_MONTHLY")
    do_weekly = (now.weekday() == 0 and do_data) or force("FORCE_WEEKLY")
    do_monthly = (now.day >= 25 and do_data) or force("FORCE_MONTHLY")

    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    seen = _load(SEEN, {})
    window = _load(NEWSSTORE, [])
    wstate = _load(WSTATE, {})
    statuses, price_rows = {}, []

    with httpx.Client(follow_redirects=True) as client:
        new_items, counts, errs, skipped = news.sweep(client, universe, seen, now.hour)
        statuses["news"] = (f"{sum(counts.values())} new across {len(counts)} queries"
                            + (f"; {len(skipped)} rotated out this hour" if skipped else "")
                            + (f"; errors: {len(errs)}" if errs else ""))
        force_all = force("FORCE_DATA") or force("FORCE_WEEKLY") or force("FORCE_MONTHLY")
        reg_items, reg_status, manual_list = watchers.sweep(client, universe, seen, wstate, now.hour, force=force_all)
        new_items += reg_items
        statuses["regulators"] = "; ".join(f"{k}={v}" for k, v in reg_status.items()) or "all rotated out"

        cutoff = int((now - dt.timedelta(hours=36)).timestamp())
        window = [i for i in window if seen.get(i["id"], 0) >= cutoff] + new_items
        keep = int((now - dt.timedelta(days=30)).timestamp())
        seen = {k: v for k, v in seen.items() if v >= keep}

        if do_data:
            r, statuses["chemicalbook"] = feeds.fetch_chemicalbook(client); price_rows += r
            r, statuses["pharmacompass"] = feeds.fetch_pharmacompass(client); price_rows += r
            r, statuses["fx"] = feeds.fetch_fx(client); price_rows += r
            r, statuses["dce_futures"] = feeds.fetch_dce(client); price_rows += r
            r, statuses["bond_quotes"] = feeds.fetch_bond(client); price_rows += r
        if do_weekly:
            metas, statuses["eastmoney_list"] = feeds.fetch_eastmoney_reports(
                client, (now.date() - dt.timedelta(days=8)).isoformat(), now.date().isoformat())
            PDFS.mkdir(parents=True, exist_ok=True)
            got, print_rows = 0, 0
            for m in metas[:12]:
                p = PDFS / f"{m['infoCode']}.pdf"
                if p.exists():
                    continue
                try:
                    resp = client.get(m["pdf"], headers=feeds.UA, timeout=60)
                    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                        p.write_bytes(resp.content); got += 1
                        ex = [x for x in feeds.extract_pdf_prints(p, m) if x.get("value") is not None]
                        print_rows += len(ex); price_rows += ex
                except Exception:
                    pass
            statuses["eastmoney_pdfs"] = f"{got} new PDFs, {print_rows} price rows extracted"
        if do_monthly:
            r, statuses["comtrade"] = feeds.fetch_comtrade(client, os.environ.get("COMTRADE_KEY") or None); price_rows += r
            r, statuses["comext"] = feeds.fetch_comext(client); price_rows += r

    statuses["tradestat_india"] = "manual (JS portal) — 29411010/30/50 monthly at tradestat.commerce.gov.in"
    statuses["gacc_8digit"] = "not scraped (captcha + foreign-IP) — buy HS6 from chinadata.live when volumes needed"
    statuses["healthoo"] = "paywalled — the real tape; Monday broker PDFs re-print it"

    if price_rows:
        append_history(price_rows)
    SEEN.write_text(json.dumps(seen), encoding="utf-8")
    NEWSSTORE.write_text(json.dumps(window, ensure_ascii=False), encoding="utf-8")
    WSTATE.write_text(json.dumps(wstate), encoding="utf-8")

    (DOCS / "centrient_news.json").write_text(json.dumps({
        "generated_at": now.isoformat(timespec="seconds") + "Z", "window_hours": 36,
        "items": sorted(window, key=lambda x: seen.get(x["id"], 0), reverse=True)[:200],
        "counts_this_run": counts, "errors_this_run": errs,
        "regulator_watch": reg_status, "manual_weekly": manual_list,
        "guardrails": universe["guardrails"],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    (DOCS / "centrient_data.json").write_text(json.dumps({
        "generated_at": now.isoformat(timespec="seconds") + "Z",
        "ran": {"news": True, "data": do_data, "weekly": do_weekly, "monthly": do_monthly},
        "statuses": statuses, "series": series_map(),
        "manual_weekly": manual_list,
        "thresholds": THRESHOLDS, "guardrails": universe["guardrails"],
        "provenance": "deterministic acquisition only; judgement happens in the brief",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("news:", statuses.get("news"), "| regulators swept | data:", do_data, "weekly:", do_weekly, "monthly:", do_monthly)

if __name__ == "__main__":
    main()

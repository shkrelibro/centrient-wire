"""Centrient wire — deterministic fetchers. No model calls; judgement happens in the brief.
Every fetcher returns (rows, status) where rows are dicts
{series, date, value, unit, source} and status is 'ok' | 'empty' | 'error: ...'.
Failures are recorded, never raised: a gap must be visible, not fatal."""
import json, re, time
import httpx

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) newsflow-datafeed/1.0"}

# ---------------- ChemicalBook (daily; RMB/tonne list quotes; reads high vs the Wind tape) ---------
CB_IDS = {
    "cb_6apa_rmb_t":        ("cb1410046",  "6-APA"),
    "cb_cefalexin_rmb_t":   ("cb1210543",  "cefalexin 头孢氨苄"),
    "cb_amoxicillin_rmb_t": ("cb3690305",  "amoxicillin 阿莫西林"),
    "cb_pengK_rmb_t":       ("cb4855484",  "penicillin G potassium salt"),
    "cb_pengK_ind_rmb_t":   ("cb00130731", "penicillin G K industrial salt"),
    "cb_7adca_rmb_t":       ("cb6505567",  "7-ADCA"),
}
_price_rx = re.compile(r"([0-9][0-9,]{2,}(?:\.[0-9]+)?)\s*元\s*/\s*(吨|千克|公斤|kg|KG)")
_meta_rx = re.compile(r"最新报价[^0-9]{0,10}([0-9][0-9,]{1,}(?:\.[0-9]+)?)\s*元\s*/\s*(吨|千克|公斤|kg|KG)")
_date_rx = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_UNIT = {"吨": "RMB/t", "千克": "RMB/kg", "公斤": "RMB/kg", "kg": "RMB/kg", "KG": "RMB/kg"}

def fetch_chemicalbook(client):
    rows, errs = [], []
    for series, (cid, label) in CB_IDS.items():
        hit = None
        for url in (f"https://m.chemicalbook.com/priceindex_{cid}.htm",
                    f"https://www.chemicalbook.com/priceindex_{cid}.htm"):
            try:
                r = client.get(url, headers=UA, timeout=25)
                if r.status_code != 200:
                    continue
                m = _price_rx.search(r.text) or _meta_rx.search(r.text)
                if m:
                    d = _date_rx.search(r.text)
                    hit = {"series": series, "date": d.group(1) if d else time.strftime("%Y-%m-%d"),
                           "value": float(m.group(1).replace(",", "")),
                           "unit": _UNIT.get(m.group(2), "RMB/?") + " (list quote)", "source": url}
                    break
            except Exception as e:
                errs.append(f"{series}: {type(e).__name__}")
            time.sleep(1.0)
        if hit:
            rows.append(hit)
        else:
            errs.append(f"{series}: no price pattern")
        time.sleep(0.8)
    return rows, ("ok" if rows and not errs else ("empty" if not rows else "partial: " + "; ".join(errs)))

# ---------------- PharmaCompass (weekly; USD/kg from Indian customs shipments) ---------------------
PHC = {"phc_6apa_usd_kg": "6-apa",
       "phc_amoxicillin_usd_kg": "amoxicillin",
       "phc_cefalexin_usd_kg": "cephalexin-monohydrate"}
def _phc_extract(text):
    i = text.find('"unitRateFc"')
    if i == -1:
        return None
    start = text.rfind("[", 0, i)
    if start == -1:
        return None
    depth, j = 0, start
    while j < len(text) and j < start + 400000:
        c = text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except Exception:
                    return None
        j += 1
    return None

def fetch_pharmacompass(client):
    rows, errs = [], []
    for series, slug in PHC.items():
        url = f"https://www.pharmacompass.com/price/{slug}"
        try:
            r = client.get(url, headers=UA, timeout=30, follow_redirects=True)
            recs = _phc_extract(r.text)
            if recs is None:
                errs.append(f"{series}: no embedded array"); continue
            recs = [x for x in recs if x.get("unitRateFc") and x.get("date")]
            if not recs:
                errs.append(f"{series}: empty array"); continue
            recs.sort(key=lambda x: str(x["date"]))
            tail = recs[-8:]
            vals = [float(x["unitRateFc"]) for x in tail if str(x.get("unit", "KGS")).upper().startswith("KG")]
            if vals:
                vals.sort()
                med = vals[len(vals)//2]
                rows.append({"series": series, "date": str(tail[-1]["date"])[:10], "value": round(med, 2),
                             "unit": "USD/kg (median of last 8 shipments)", "source": url})
        except Exception as e:
            errs.append(f"{series}: {type(e).__name__}")
        time.sleep(1.5)
    return rows, ("ok" if rows and not errs else ("empty" if not rows else "partial: " + "; ".join(errs)))

# ---------------- Eastmoney broker PDFs (weekly; the free re-print of the Wind/Healthoo tape) ------
_report_kw = re.compile(r"(医药|原料药|抗生素|周报|月报)")
PRINT_PATTERNS = {
    "brk_6apa_rmb_kg":      re.compile(r"6[-‐]?APA[^0-9]{0,15}([0-9]{2,3}(?:\.[0-9])?)\s*元"),
    "brk_pengsalt_rmb_bou": re.compile(r"青霉素工业盐[^0-9]{0,15}([0-9]{2,3}(?:\.[0-9])?)\s*元"),
    "brk_7adca_rmb_kg":     re.compile(r"7[-‐]?ADCA[^0-9]{0,15}([0-9]{2,4}(?:\.[0-9])?)\s*元"),
    "brk_cefalexin_rmb_kg": re.compile(r"头孢氨苄[^0-9]{0,15}([0-9]{2,4}(?:\.[0-9])?)\s*元"),
}

def fetch_eastmoney_reports(client, begin, end):
    metas, errs = [], []
    for page in (1, 2, 3):
        try:
            r = client.get("https://reportapi.eastmoney.com/report/list",
                           params={"industryCode": "*", "industry": "*", "rating": "*", "ratingChange": "*",
                                   "beginTime": begin, "endTime": end, "pageNo": str(page),
                                   "pageSize": "100", "qType": "1"},
                           headers={**UA, "Referer": "https://data.eastmoney.com/"}, timeout=30)
            data = r.json().get("data") or []
            for rec in data:
                title = rec.get("title", "")
                if _report_kw.search(title + rec.get("industryName", "")):
                    metas.append({"infoCode": rec.get("infoCode"), "title": title,
                                  "org": rec.get("orgSName"), "date": str(rec.get("publishDate", ""))[:10],
                                  "pdf": f"https://pdf.dfcfw.com/pdf/H3_{rec.get('infoCode')}_1.pdf"})
            if not data:
                break
        except Exception as e:
            errs.append(f"page{page}: {type(e).__name__}")
        time.sleep(2.0)
    return metas, ("ok" if metas else ("empty" if not errs else "error: " + "; ".join(errs)))

def extract_pdf_prints(pdf_path, meta):
    """Best-effort price extraction from one broker PDF. Returns print rows with a snippet each."""
    rows = []
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:14])
    except Exception as e:
        return [{"series": "brk_error", "date": meta["date"], "value": None,
                 "unit": type(e).__name__, "source": meta["pdf"]}]
    for series, rx in PRINT_PATTERNS.items():
        m = rx.search(text)
        if m:
            i = max(0, m.start() - 20)
            rows.append({"series": series, "date": meta["date"], "value": float(m.group(1)),
                         "unit": ("RMB/BOU" if "bou" in series else "RMB/kg"),
                         "source": meta["pdf"],
                         "snippet": text[i:m.end() + 15].replace("\n", " ")})
    return rows

# ---------------- Trade mirror (monthly; laggy by construction — context, not spot) ----------------
def _last_periods(n):
    import datetime as dt
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        d = (d - dt.timedelta(days=1)).replace(day=1)
        out.append(d.strftime("%Y%m"))
    return out

def fetch_comtrade(client, key=None):
    rows, errs = [], []
    periods = ",".join(_last_periods(12))
    for series, cmd in (("ct_cn_export_294110", "294110"), ("ct_cn_export_294190", "294190")):
        try:
            if key:
                url = "https://comtradeapi.un.org/data/v1/get/C/M/HS"
                params = {"reporterCode": "156", "flowCode": "X", "cmdCode": cmd,
                          "period": periods, "subscription-key": key}
            else:
                url = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
                params = {"reporterCode": "156", "flowCode": "X", "cmdCode": cmd, "period": periods}
            r = client.get(url, params=params, headers=UA, timeout=40)
            recs = (r.json().get("data") or [])
            world = [x for x in recs if str(x.get("partnerCode")) == "0"]
            if world:
                world.sort(key=lambda x: str(x.get("period")))
                w = world[-1]
                val, kg = float(w.get("primaryValue") or 0), float(w.get("netWgt") or 0)
                rows.append({"series": series + "_usd", "date": str(w["period"]), "value": round(val),
                             "unit": "USD (world, month)", "source": "comtradeapi.un.org"})
                if kg > 0:
                    rows.append({"series": series + "_usd_kg", "date": str(w["period"]),
                                 "value": round(val / kg, 2), "unit": "USD/kg (blended)",
                                 "source": "comtradeapi.un.org"})
            else:
                errs.append(f"{series}: no world rows")
        except Exception as e:
            errs.append(f"{series}: {type(e).__name__}")
        time.sleep(1.5)
    return rows, ("ok" if rows and not errs else ("empty" if not rows else "partial: " + "; ".join(errs)))

def fetch_comext(client):
    """EU imports from China, CN8 29411000/29419000, monthly EUR + kg. The Comext dissemination
    endpoint refuses unfiltered pulls and its SDMX key layout has shifted between dataset
    generations; treat this leg as experimental until the first green run, and fall back to the
    monthly bulk CSVs if it stays red (see UPLOAD_GUIDE)."""
    base = "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data/DS-045409"
    rows, errs = [], []
    for series, code in (("cx_eu_import_29411000", "29411000"), ("cx_eu_import_29419000", "29419000")):
        try:
            r = client.get(f"{base}/M.{code}.CN.EU27_2020.1.VALUE_IN_EUROS",
                           params={"format": "JSON", "lastTimePeriod": "6"}, headers=UA, timeout=40)
            if r.status_code != 200:
                errs.append(f"{series}: HTTP {r.status_code}"); continue
            j = r.json()
            vals = j.get("value") or {}
            times = (j.get("dimension", {}).get("time", {}).get("category", {}).get("index", {}))
            if vals and times:
                inv = {v: k for k, v in times.items()}
                last_i = max(int(k) for k in vals.keys())
                rows.append({"series": series + "_eur", "date": inv.get(last_i, "?"),
                             "value": round(float(vals[str(last_i)])), "unit": "EUR (month)",
                             "source": base})
            else:
                errs.append(f"{series}: empty response")
        except Exception as e:
            errs.append(f"{series}: {type(e).__name__}")
        time.sleep(1.5)
    return rows, ("ok" if rows and not errs else ("empty" if not rows else "error: " + "; ".join(errs)))

# ---------------- FX (keyless, ECB via Frankfurter; daily ~16:00 CET) ------------------------------
def fetch_fx(client):
    try:
        j = None
        for url, params in (("https://api.frankfurter.dev/v2/rates", {"base": "usd", "quotes": "cny,eur"}),
                            ("https://api.frankfurter.app/latest", {"from": "USD", "to": "CNY,EUR"})):
            try:
                r = client.get(url, params=params, headers=UA, timeout=20)
                cand = r.json()
                if isinstance(cand, dict) and cand.get("rates"):
                    j = cand; break
            except Exception:
                continue
        if not isinstance(j, dict):
            return [], "empty (both endpoints)"
        d, rates = j.get("date"), j.get("rates", {})
        rows = []
        if rates.get("CNY"):
            rows.append({"series": "fx_usdcny", "date": d, "value": float(rates["CNY"]),
                         "unit": "CNY per USD (ECB)", "source": "api.frankfurter.dev"})
        if rates.get("EUR"):
            rows.append({"series": "fx_usdeur", "date": d, "value": float(rates["EUR"]),
                         "unit": "EUR per USD (ECB)", "source": "api.frankfurter.dev"})
        return rows, ("ok" if rows else "empty")
    except Exception as e:
        return [], f"error: {type(e).__name__}"

# ---------------- DCE corn & corn starch (cost floor; Sina daily kline, Referer-gated) -------------
_SINA_REF = {**UA, "Referer": "https://finance.sina.com.cn/"}

def fetch_dce(client):
    rows, errs = [], []
    # realtime quote line first (mandatory Referer; GBK body)
    try:
        r = client.get("https://hq.sinajs.cn/list=nf_C0,nf_CS0", headers=_SINA_REF, timeout=20)
        body = r.content.decode("gbk", "ignore")
        import re as _re
        for series, tag in (("dce_corn_rmb_t", "nf_C0"), ("dce_cornstarch_rmb_t", "nf_CS0")):
            m = _re.search(tag + r'="([^"]+)"', body)
            if not m:
                errs.append(f"{series}: no rt line"); continue
            f = m.group(1).split(",")
            # layout: name, ?, open, high, low, prev_close, bid, ask, last, settle, prev_settle, ... , date(last)
            px = next((x for x in (f[8] if len(f) > 8 else "", f[9] if len(f) > 9 else "") if x and x != "0"), "")
            date = next((x for x in reversed(f) if _re.match(r"20\d\d-\d\d-\d\d", x)), time.strftime("%Y-%m-%d"))
            if px:
                rows.append({"series": series, "date": date, "value": float(px),
                             "unit": "RMB/t (rt last/settle)", "source": "sina:hq:" + tag})
            else:
                errs.append(f"{series}: rt empty")
    except Exception as e:
        errs.append(f"rt: {type(e).__name__}")
    if not rows:  # kline fallback with staleness guard
        for series, sym in (("dce_corn_rmb_t", "C0"), ("dce_cornstarch_rmb_t", "CS0")):
            try:
                r = client.get("https://stock2.finance.sina.com.cn/futures/api/json.php/"
                               "IndexService.getInnerFuturesDailyKLine", params={"symbol": sym},
                               headers=_SINA_REF, timeout=25)
                data = r.json()
                if isinstance(data, list) and data:
                    last = data[-1]
                    age_ok = str(last[0]) >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
                    rows.append({"series": series, "date": str(last[0]), "value": float(last[4]),
                                 "unit": "RMB/t (kline%s)" % ("" if age_ok else " STALE"),
                                 "source": f"sina:kline:{sym}"})
                    if not age_ok:
                        errs.append(f"{series}: kline stale {last[0]}")
                else:
                    errs.append(f"{series}: kline empty")
            except Exception as e:
                errs.append(f"{series}: {type(e).__name__}")
            time.sleep(1.0)
    return rows, ("ok" if rows and not errs else ("empty" if not rows else "partial: " + "; ".join(errs)))

# ---------------- Centrient bond indicative quotes (Boerse Frankfurt computed-header API) ----------
_BF_SALT = "w4icATTGtnjAZMbkL3kJwxMfEAKDa3MN"
BOND_ISINS = {"bond_675_2030": "XS3045391607", "bond_frn_2030": "XS3045393306"}

def _bf_headers(url):
    import datetime as _dt, hashlib as _h
    client_date = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.") + f"{_dt.datetime.utcnow().microsecond//1000:03d}Z"
    trace = _h.md5((client_date + url + _BF_SALT).encode()).hexdigest()
    try:
        from zoneinfo import ZoneInfo
        local = _dt.datetime.now(ZoneInfo("Europe/Berlin"))
    except Exception:
        local = _dt.datetime.utcnow()
    sec = _h.md5(local.strftime("%Y%m%d%H%M").encode()).hexdigest()
    return {**UA, "Client-Date": client_date, "X-Client-TraceId": trace, "X-Security": sec,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.boerse-frankfurt.de", "Referer": "https://www.boerse-frankfurt.de/"}

def fetch_bond(client):
    rows, errs = [], []
    for series, isin in BOND_ISINS.items():
        url = f"https://api.boerse-frankfurt.de/v1/data/price_information?isin={isin}&mic=XFRA"
        try:
            r = client.get(url, headers=_bf_headers(url), timeout=25)
            j = r.json() if r.status_code == 200 else {}
            px = j.get("lastPrice") or j.get("price") or (j.get("data") or {}).get("lastPrice")
            if px:
                rows.append({"series": series, "date": time.strftime("%Y-%m-%d"), "value": float(px),
                             "unit": "pct of par (indicative, XFRA)", "source": url})
            else:
                # Tradegate fallback (German decimal comma in bid/ask/last spans)
                try:
                    tg = client.get(f"https://www.tradegate.de/orderbuch.php?isin={isin}",
                                    headers=UA, timeout=20)
                    import re as _re
                    mm = _re.search(r'id="(?:last|bid)"[^>]*>\s*([0-9]{1,3},[0-9]{1,4})', tg.text)
                    if mm:
                        rows.append({"series": series, "date": time.strftime("%Y-%m-%d"),
                                     "value": float(mm.group(1).replace(",", ".")),
                                     "unit": "pct of par (Tradegate indicative)",
                                     "source": f"tradegate:{isin}"})
                    else:
                        errs.append(f"{series}: empty (XFRA header scheme + Tradegate both blank; Stuttgart manual)")
                except Exception:
                    errs.append(f"{series}: empty (XFRA + Tradegate failed; Stuttgart manual)")
        except Exception as e:
            errs.append(f"{series}: {type(e).__name__}")
        time.sleep(1.0)
    return rows, ("ok" if rows and not errs else ("empty: " + "; ".join(errs) if not rows else "partial: " + "; ".join(errs)))

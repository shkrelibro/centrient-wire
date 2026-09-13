"""Deterministic news sweep: Google News RSS (+ Bing on flagged queries) across the editions in
config/universe.yaml. Language groups (langs: eu_blanket) and hourly rotation (every: N -> run on
hours where hour % N == 0) keep the request budget polite. No model calls; every item carries
provenance and a deterministic trip-wire tag; recall is auditable via per-query counts."""
import hashlib, re, time
from urllib.parse import quote
import feedparser

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) centrient-wire/2.0"}

def _gnews_url(q, L):
    return (f"https://news.google.com/rss/search?q={quote(q)}"
            f"&hl={L['hl']}&gl={L['gl']}&ceid={quote(L['ceid'])}")

def _bing_url(q):
    return f"https://www.bing.com/news/search?q={quote(q)}&format=RSS"

def _iid(title, link):
    return hashlib.sha1((title.strip() + "|" + link.split("?")[0]).encode("utf-8")).hexdigest()[:16]

def _resolve_langs(spec, universe):
    langs = spec.get("langs", ["en"])
    if isinstance(langs, str):
        langs = universe.get("lang_groups", {}).get(langs, ["en"])
    return langs

def sweep(client, universe, seen, hour):
    langs_tbl = universe["languages"]
    trip_rx = re.compile(universe["tripwire_regex"], re.I)
    items, counts, errors, skipped = [], {}, [], []
    for spec in universe["queries"]:
        qid, q = spec["id"], spec["q"]
        if hour % int(spec.get("every", 1)) != 0:
            skipped.append(qid); continue
        urls = [(f"google:{lg}", _gnews_url(q, langs_tbl[lg]), lg)
                for lg in _resolve_langs(spec, universe) if lg in langs_tbl]
        if "bing" in spec.get("routes", []):
            urls.append(("bing:en", _bing_url(q), "en"))
        got = 0
        for route, url, lg in urls:
            try:
                r = client.get(url, headers=UA, timeout=25)
                feed = feedparser.parse(r.text)
                for e in feed.entries[:25]:
                    title = getattr(e, "title", "").strip()
                    link = getattr(e, "link", "")
                    if not title or not link:
                        continue
                    iid = _iid(title, link)
                    if iid in seen:
                        continue
                    src = ""
                    if getattr(e, "source", None) is not None:
                        src = getattr(e.source, "title", "") or ""
                    items.append({"id": iid, "query": qid, "route": route, "lang": lg,
                                  "title": title, "url": link, "source": src,
                                  "published": getattr(e, "published", "")[:31],
                                  "trip": bool(trip_rx.search(title))})
                    seen[iid] = int(time.time())
                    got += 1
            except Exception as ex:
                errors.append(f"{qid}/{route}: {type(ex).__name__}")
            time.sleep(0.8)
        counts[qid] = got
    return items, counts, errors, skipped

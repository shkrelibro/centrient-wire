"""Direct regulator sweeps: type rss parses a feed and emits unseen entries; type page fetches the
URL, strips tags, hashes the text and emits ONE '<name>: page updated' item when the hash moves;
type manual_weekly is never fetched, only surfaced in data health. Deterministic; state lives in
data/watch_state.json ({regulator_id: last_hash})."""
import hashlib, re, time
import feedparser
from news import UA, _iid

_tag_rx = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)
_ws_rx = re.compile(r"\s+")

def _text_hash(html):
    txt = _ws_rx.sub(" ", _tag_rx.sub(" ", html)).strip()
    return hashlib.sha1(txt.encode("utf-8", "ignore")).hexdigest(), len(txt)

def sweep(client, universe, seen, state, hour, force=False):
    items, statuses, manual = [], {}, []
    for spec in universe.get("regulators", []):
        rid, name, typ = spec["id"], spec["name"], spec["type"]
        if typ == "manual_weekly":
            manual.append({"id": rid, "name": name, "url": spec["url"], "note": spec.get("note", "")})
            continue
        if not force and hour % int(spec.get("every", 6)) != 0:
            statuses[rid] = "skipped (rotation)"; continue
        try:
            r = client.get(spec["url"], headers=UA, timeout=30)
            if typ == "rss":
                feed = feedparser.parse(r.text)
                got = 0
                for e in feed.entries[:15]:
                    title, link = getattr(e, "title", "").strip(), getattr(e, "link", spec["url"])
                    if not title:
                        continue
                    iid = _iid(f"{rid}|{title}", link)
                    if iid in seen:
                        continue
                    items.append({"id": iid, "query": rid, "route": "rss", "lang": "en",
                                  "title": f"{name}: {title}", "url": link, "source": name,
                                  "published": getattr(e, "published", "")[:31],
                                  "trip": bool(spec.get("trip", False))})
                    seen[iid] = int(time.time()); got += 1
                statuses[rid] = f"ok ({got} new)"
            else:  # page hash-diff
                h, n = _text_hash(r.text)
                prev = state.get(rid)
                state[rid] = h
                if prev is None:
                    statuses[rid] = f"baseline set ({n} chars)"
                elif prev != h:
                    iid = _iid(f"{rid}|{h}", spec["url"])
                    items.append({"id": iid, "query": rid, "route": "page", "lang": "en",
                                  "title": f"{name}: page updated", "url": spec["url"],
                                  "source": name, "published": "", "trip": bool(spec.get("trip", False))})
                    seen[iid] = int(time.time())
                    statuses[rid] = "CHANGED"
                else:
                    statuses[rid] = "unchanged"
                if spec.get("note"):
                    statuses[rid] += f" [{spec['note']}]"
        except Exception as ex:
            statuses[rid] = f"error: {type(ex).__name__}"
        time.sleep(1.0)
    return items, statuses, manual

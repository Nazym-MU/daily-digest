"""One "Read / Talk of the Day": a single item, picked from a pool of people worth
listening to, in place of a feed of headlines.

Pool:
  - Paul Graham essays. A new essay always wins the day; PG posts a few times a year.
  - LessWrong's top post of the last few days (karma floor).
  - Talks / interviews from a handful of YouTube channels (free RSS feeds).

Without a new PG essay, the day alternates between LessWrong and a talk, falling back
to the other when one has nothing new. Talks are ranked by views relative to their
own channel's median, so a standout Anthropic talk can beat an average YC upload.

Each item carries a short `key` (e.g. "yt:<videoId>", "lw:<postId>", "pg:<slug>") that
fits Telegram's 64-byte callback_data; listener.py uses it to fetch the full text or
transcript when she taps Distill.
"""

import json
import re
import sys
import html
import statistics
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEEN_FILE = Path(__file__).resolve().parent / ".seen_reads.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) daily-digest-bot/1.0"
TIMEOUT = 30

PG_INDEX = "https://paulgraham.com/articles.html"
LESSWRONG_GRAPHQL = "https://www.lesswrong.com/graphql"
LW_MIN_KARMA = 100
LW_WINDOW_DAYS = 4

# Handle -> channel id (YouTube RSS needs the id). Add or drop channels here.
CHANNELS = {
    "Anthropic": "UCrDwWp7EBBv4NwvScIpBDOA",
    "Andrej Karpathy": "UCXUPKJO5MZQN11PqgIvyuvQ",
    "Dwarkesh Patel": "UCXl4i9dYBrFOabk0xGmbkRA",
    "Y Combinator": "UCcefcZRL2oaA_uBNeo5UOWg",
    "Latent Space": "UCxBcwypKK-W3GHd_RZ9FZrQ",
    "AI Engineer": "UCLKPca3kwwd-B59HNr-_lvA",
}
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
TALK_WINDOW_DAYS = 14
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}


def _get(url, data=None, headers=None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _load_seen():
    try:
        return json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError):
        return {}


def mark_seen(item):
    """Call only after a successful send."""
    if not item:
        return
    seen = _load_seen()
    keys = seen.setdefault("keys", [])
    if item["key"] not in keys:
        keys.append(item["key"])
    seen["keys"] = keys[-2000:]
    if "pg_slugs" in item:  # first run: bank the PG back catalogue so it isn't "new"
        seen["pg_seeded"] = True
        seen["keys"] = list(dict.fromkeys(seen["keys"] + [f"pg:{s}" for s in item["pg_slugs"]]))
    SEEN_FILE.write_text(json.dumps(seen))


# --------------------------------------------------------------------------- sources

def _pg_new(seen):
    try:
        page = _get(PG_INDEX)
    except urllib.error.URLError as e:
        print(f"[reads] PG index failed: {e}", file=sys.stderr)
        return None, []
    links = re.findall(r'<a href="([a-z0-9]+)\.html">([^<]+)</a>', page)
    slugs = [s for s, _ in links if s not in ("index", "articles")]
    titles = dict(links)
    if not slugs:
        return None, []
    if not seen.get("pg_seeded"):
        # Cold start (fresh cache): the newest essay counts as new, the rest as read.
        return {"slug": slugs[0], "title": html.unescape(titles[slugs[0]])}, slugs
    known = set(seen.get("keys", []))
    for slug in slugs[:5]:  # new essays go to the top of the list
        if f"pg:{slug}" not in known:
            return {"slug": slug, "title": html.unescape(titles[slug])}, []
    return None, []


def _lesswrong(seen):
    after = (datetime.now(timezone.utc) - timedelta(days=LW_WINDOW_DAYS)).isoformat()
    query = """query($after: String) { posts(input: {terms: {view: "top", after: $after, limit: 15}}) {
      results { _id title pageUrl baseScore user { displayName } } } }"""
    payload = json.dumps({"query": query, "variables": {"after": after}}).encode()
    try:
        res = json.loads(_get(LESSWRONG_GRAPHQL, data=payload,
                              headers={"Content-Type": "application/json"}))["data"]["posts"]["results"]
    except (urllib.error.URLError, KeyError, TypeError, ValueError) as e:
        print(f"[reads] LessWrong failed: {e}", file=sys.stderr)
        return None
    known = set(seen.get("keys", []))
    for p in sorted(res, key=lambda p: -(p.get("baseScore") or 0)):
        if (p.get("baseScore") or 0) >= LW_MIN_KARMA and f"lw:{p['_id']}" not in known:
            return {"key": f"lw:{p['_id']}", "kind": "essay", "source": "LessWrong",
                    "title": p["title"], "url": p["pageUrl"],
                    "meta": f"{p['baseScore']} karma · {(p.get('user') or {}).get('displayName') or 'unknown'}"}
    return None


def _is_short(video_id):
    """/shorts/<id> answers 200 for a Short and redirects for a normal video."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        req = urllib.request.Request(f"https://www.youtube.com/shorts/{video_id}", method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        return opener.open(req, timeout=TIMEOUT).status == 200
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError:
        return False


def _talk(seen):
    cutoff = datetime.now(timezone.utc) - timedelta(days=TALK_WINDOW_DAYS)
    known = set(seen.get("keys", []))
    scored = []
    for name, cid in CHANNELS.items():
        try:
            root = ET.fromstring(_get(YT_FEED.format(cid)))
        except (urllib.error.URLError, ET.ParseError) as e:
            print(f"[reads] feed {name} failed: {e}", file=sys.stderr)
            continue
        entries = []
        for e in root.findall("a:entry", NS):
            vid = e.findtext("yt:videoId", namespaces=NS)
            stats = e.find("media:group/media:community/media:statistics", NS)
            views = int(stats.get("views", 0)) if stats is not None else 0
            published = datetime.fromisoformat(e.findtext("a:published", namespaces=NS))
            entries.append((vid, e.findtext("a:title", namespaces=NS), views, published))
        if not entries:
            continue
        median = statistics.median(v for _, _, v, _ in entries) or 1
        for vid, title, views, published in entries:
            if published >= cutoff and f"yt:{vid}" not in known:
                scored.append((views / median, vid, title, views, name))
    for _, vid, title, views, name in sorted(scored, reverse=True)[:8]:
        if _is_short(vid):
            continue
        return {"key": f"yt:{vid}", "kind": "talk", "source": name, "title": title,
                "url": f"https://www.youtube.com/watch?v={vid}", "meta": f"{name} · {views:,} views"}
    return None


# --------------------------------------------------------------------------- pick

def fetch_read(day_index):
    seen = _load_seen()
    pg, backlog = _pg_new(seen)
    if pg:
        item = {"key": f"pg:{pg['slug']}", "kind": "essay", "source": "Paul Graham",
                "title": pg["title"], "url": f"https://paulgraham.com/{pg['slug']}.html",
                "meta": "Paul Graham · new essay"}
        if backlog:
            item["pg_slugs"] = backlog
        return item
    order = (_talk, _lesswrong) if day_index % 2 == 0 else (_lesswrong, _talk)
    for fetch in order:
        item = fetch(seen)
        if item:
            return item
    return None


def format_read_html(item):
    esc = lambda s: html.escape(str(s))  # noqa: E731
    head = "<b>🎙 Talk of the Day</b>" if item["kind"] == "talk" else "<b>🧭 Read of the Day</b>"
    return [head,
            f'• <a href="{esc(item["url"])}">{esc(item["title"])}</a>',
            f'  <i>{esc(item["meta"])}</i>']


if __name__ == "__main__":  # preview without sending or marking seen
    for d in (0, 1):
        print(d, json.dumps({k: v for k, v in (fetch_read(d) or {}).items() if k != "pg_slugs"},
                            ensure_ascii=False))

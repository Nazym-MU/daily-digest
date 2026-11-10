#!/usr/bin/env python3
"""Find upcoming Bay Area Luma events that match my interests.

Source is Luma's public **discover** feed (the data behind luma.com/discover).
Luma's search endpoint needs a signed-in session, and the official API only sees
calendars you manage, so neither works from a CI runner. The discover feed is
anonymous and supports a category plus a location, which covers it.

The feed only exposes event names (no descriptions), so matching is a keyword
pass over the event name and its calendar name. Generic "mixer" and "happy hour"
events are skipped on purpose: a sample of two weeks had ~90 of them. The
community topic keeps the kind of event where people meet over an idea
(reading groups, paper clubs, research clubs).

Events already sent are remembered in `.seen_events.json`, so each event shows up
once. When nothing new matches, the section is omitted from the digest.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

DISCOVER_URL = "https://api.lu.ma/discover/get-paginated-events"
EVENT_URL = "https://luma.com/{}"

USER_AGENT = "daily-digest-bot/1.0 (+https://github.com/)"
TIMEOUT = 30

# San Francisco. Passed explicitly because the feed otherwise localizes by IP,
# and the GitHub runner is not in the Bay Area.
HOME_LAT, HOME_LNG = 37.7749, -122.4194
MAX_DISTANCE_KM = float(os.environ.get("EVENTS_MAX_KM", "70"))  # SF to San Jose ~68
WINDOW_DAYS = int(os.environ.get("EVENTS_WINDOW_DAYS", "14"))
MAX_EVENTS = int(os.environ.get("EVENTS_MAX", "6"))
MAX_PAGES = 12  # per source, 50 events a page
REQUEST_PAUSE = float(os.environ.get("LUMA_PAUSE", "1.5"))

# Each feed is paginated separately and the results are merged by event id.
SOURCES = [
    {"discover_category_api_id": "cat-ai"},
    {"discover_category_api_id": "cat-tech"},
    {"discover_place_api_id": "discplace-BDj7GNbGlsF7Cka"},  # San Francisco
]

SEEN_PATH = os.environ.get(
    "EVENTS_SEEN_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".seen_events.json"),
)
SEEN_LIMIT = 500

# Topic -> regex over "event name | calendar name". Order sets the label shown
# when an event matches several.
TOPICS = {
    "3D": r"\b3d\b(?!-dram)|gaussian splat|splatting|\bnerf\b|neural render|graphics|"
          r"rendering|spatial (computing|intelligence)|spatial frontier|blender|"
          r"computer vision|siggraph|\bcuda\b|gpu kernel",
    "world models": r"world model|embodied|physical (ai|intelligence)",
    "math": r"\bmath|mathemat|theorem|\blean (meetup|4|prover)|sf lean|"
            r"formal (proof|verification|methods)|olympiad|topology|"
            r"category theory|number theory|linear algebra",
    "AI × science": r"ai (for|in|x) science|ai4science|for science|scientific|"
                    r"physics|biology|bioml|protein|molecul|neuroscience|"
                    r"materials science|drug discovery|fluid",
    "community": r"reading group|paper club|paper discussion|research club|"
                 r"journal club|math club|study group|hack night",
}
TOPIC_RES = {name: re.compile(rx, re.I) for name, rx in TOPICS.items()}

TOPIC_EMOJI = {
    "3D": "🧊",
    "world models": "🌍",
    "math": "∑",
    "AI × science": "🧪",
    "community": "👥",
}


def _get(params):
    # Luma answers a burst of ~40 requests with a temporary 403
    # (auth/additional-verification-required); a pause keeps the run under that.
    time.sleep(REQUEST_PAUSE)
    url = f"{DISCOVER_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_source(source, until, max_pages=MAX_PAGES):
    """All entries from one feed, stopping once events start past `until`."""
    entries, cursor = [], None
    for _ in range(max_pages):
        params = {**source, "latitude": HOME_LAT, "longitude": HOME_LNG,
                  "pagination_limit": 50}
        if cursor:
            params["pagination_cursor"] = cursor
        data = _get(params)
        page = data.get("entries") or []
        entries.extend(page)
        # The feed is sorted by start time, so once a page ends past the window
        # the rest is too.
        if not data.get("has_more") or not page or _start(page[-1]) > until:
            break
        cursor = data.get("next_cursor")
    return entries


def _start(entry):
    raw = (entry.get("event") or {}).get("start_at") or entry.get("start_at")
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _distance_km(coord):
    lat1, lng1, lat2, lng2 = map(radians, (HOME_LAT, HOME_LNG,
                                           coord["latitude"], coord["longitude"]))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def _topics(text):
    return [name for name, rx in TOPIC_RES.items() if rx.search(text)]


def _clean(entry, topics):
    event = entry["event"]
    geo = event.get("geo_address_info") or {}
    return {
        "id": event["api_id"],
        "name": event.get("name") or "(untitled)",
        "url": EVENT_URL.format(event.get("url") or event["api_id"]),
        "start": _start(entry),
        "timezone": event.get("timezone") or "America/Los_Angeles",
        "city": geo.get("city") or "",
        "host": (entry.get("calendar") or {}).get("name") or "",
        "topics": topics,
    }


def load_seen():
    """Ids already sent. Missing or corrupt cache is not fatal — just start over."""
    try:
        with open(SEEN_PATH) as fh:
            return list(json.load(fh).get("ids", []))
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def mark_seen(events):
    """Record events as sent. Call only after the digest actually went out."""
    ids = load_seen() + [e["id"] for e in events]
    try:
        with open(SEEN_PATH, "w") as fh:
            json.dump({"ids": ids[-SEEN_LIMIT:]}, fh)
    except OSError as e:
        print(f"[luma] could not persist seen-cache: {e}", file=sys.stderr)


def fetch_events(skip_seen=True, now=None):
    """New, in-person Bay Area events in the next WINDOW_DAYS that match TOPICS.

    Returns at most MAX_EVENTS, soonest first. Anything left over stays unseen,
    so it is offered again tomorrow while it is still upcoming.
    """
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(days=WINDOW_DAYS)
    seen = set(load_seen()) if skip_seen else set()

    entries = {}
    for source in SOURCES:
        try:
            for entry in _fetch_source(source, until):
                entries[entry["event"]["api_id"]] = entry
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[luma] {source} failed: {e}", file=sys.stderr)

    matches = []
    for event_id, entry in entries.items():
        event = entry["event"]
        if event_id in seen or event.get("location_type") != "offline":
            continue
        if not now <= _start(entry) <= until:
            continue
        coord = event.get("coordinate")
        if not coord or _distance_km(coord) > MAX_DISTANCE_KM:
            continue
        host = (entry.get("calendar") or {}).get("name") or ""
        topics = _topics(f"{event.get('name', '')} | {host}")
        if topics:
            matches.append(_clean(entry, topics))

    matches.sort(key=lambda e: e["start"])
    return matches[:MAX_EVENTS]


def format_events_html(events):
    """Render the events section for Telegram (parse_mode=HTML)."""
    import html

    def esc(s):
        return html.escape(str(s))

    lines = ["<b>📍 Events on Luma</b>"]
    for e in events:
        local = e["start"].astimezone(ZoneInfo(e["timezone"]))
        clock = local.strftime("%-I:%M%p").replace(":00", "").lower()
        when = f'{local.strftime("%a %b %-d")}, {clock}'
        meta = [when, e["city"], " ".join(TOPIC_EMOJI[t] for t in e["topics"])]
        lines.append(f'• <a href="{esc(e["url"])}">{esc(e["name"])}</a>')
        lines.append(f'  <i>{esc(" · ".join(m for m in meta if m))}</i>')
    return lines



# ---------------------------------------------------------------------------
# Life outside tech: Luma's non-tech categories near SF (sports, running, books,
# games, food, arts). Same feed and seen-cache as above, its own digest section.
# These categories are small (a page or two of SF events each), so ~8 requests.

LIFE_SOURCES = [  # (category id, emoji), in priority order
    ("cat-0Km9ZnuBjFAjwFl", "🏐"),  # Fitness: tennis, climbing, soccer...
    ("cat-running", "🏃"),
    ("cat-books", "📚"),
    ("cat-games", "🎲"),
    ("cat-fooddrink", "🍝"),
    ("cat-AzVAf6VmE9JEre4", "🎨"),  # Arts & Culture
]
LIFE_MAX_PAGES = 3
LIFE_MAX_KM = float(os.environ.get("LIFE_MAX_KM", "25"))  # SF, Oakland, Berkeley
LIFE_WINDOW_DAYS = int(os.environ.get("LIFE_WINDOW_DAYS", "10"))
LIFE_MAX = int(os.environ.get("LIFE_MAX", "6"))
# Listed first whatever the category: what she most wants to find.
LIFE_FIRST = re.compile(r"volleyball|soccer|football|futsal|pick-?up|"
                        r"meet new people|make friends", re.I)
LIFE_FIRST_MAX = 2  # so the section isn't all soccer
# Tech networking in a lifestyle costume; the tech section already covers it.
LIFE_SKIP = re.compile(r"mixer|happy hour|networking|pitch|demo day|webinar", re.I)


def fetch_life_events(skip_seen=True, now=None, exclude=()):
    """New in-person events near SF from the non-tech categories.

    `exclude` takes ids already picked by the tech section so nothing shows twice.
    """
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(days=LIFE_WINDOW_DAYS)
    seen = (set(load_seen()) if skip_seen else set()) | set(exclude)

    # Recurring events (a weekly soccer game) get a new id each week; remember the
    # name too so a series is offered once, not every week.
    picks, names = {}, set()
    for rank, (cat, emoji) in enumerate(LIFE_SOURCES):
        try:
            entries = _fetch_source({"discover_category_api_id": cat}, until,
                                    max_pages=LIFE_MAX_PAGES)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[luma-life] {cat} failed: {e}", file=sys.stderr)
            continue
        for entry in sorted(entries, key=_start):
            event = entry["event"]
            event_id = event["api_id"]
            name_key = _name_key(event.get("name") or "")
            if (event_id in seen or name_key in seen or event_id in picks or name_key in names
                    or event.get("location_type") != "offline"):
                continue
            if not now <= _start(entry) <= until:
                continue
            coord = event.get("coordinate")
            if not coord or _distance_km(coord) > LIFE_MAX_KM:
                continue
            host = (entry.get("calendar") or {}).get("name") or ""
            text = f"{event.get('name', '')} | {host}"
            if LIFE_SKIP.search(text):
                continue
            e = _clean(entry, [])
            e.update(emoji=emoji, rank=rank, name_key=name_key,
                     first=bool(LIFE_FIRST.search(text)))
            picks[event_id] = e
            names.add(name_key)

    # The sports she's looking for first (capped), then one per category in turn,
    # soonest first within each, until the section is full.
    chosen = [e for e in sorted(picks.values(), key=lambda e: e["start"]) if e["first"]]
    chosen = chosen[:LIFE_FIRST_MAX]
    by_cat = {}
    for e in sorted(picks.values(), key=lambda e: (e["rank"], e["start"])):
        if e not in chosen:
            by_cat.setdefault(e["rank"], []).append(e)
    while len(chosen) < LIFE_MAX and any(by_cat.values()):
        for rank in sorted(by_cat):
            if by_cat[rank] and len(chosen) < LIFE_MAX:
                chosen.append(by_cat[rank].pop(0))
    return sorted(chosen, key=lambda e: e["start"])


def _name_key(name):
    return "name:" + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def mark_life_seen(events):
    """Like mark_seen, but also records each event's name (see fetch_life_events)."""
    mark_seen([{"id": x} for e in events for x in (e["id"], e["name_key"])])


def format_life_html(events):
    import html

    def esc(s):
        return html.escape(str(s))

    lines = ["<b>🌿 Life in SF on Luma</b>"]
    for e in events:
        local = e["start"].astimezone(ZoneInfo(e["timezone"]))
        clock = local.strftime("%-I:%M%p").replace(":00", "").lower()
        meta = [f'{local.strftime("%a %b %-d")}, {clock}', e["city"], e["emoji"]]
        lines.append(f'• <a href="{esc(e["url"])}">{esc(e["name"])}</a>')
        lines.append(f'  <i>{esc(" · ".join(m for m in meta if m))}</i>')
    return lines

if __name__ == "__main__":
    # Manual check, ignores the seen-cache: python3 events.py
    found = fetch_events(skip_seen=False)
    print(f"{len(found)} event(s)\n")
    print("\n".join(format_events_html(found)))
    life = fetch_life_events(skip_seen=False, exclude=[e["id"] for e in found])
    print(f"\n{len(life)} life event(s)\n")
    print("\n".join(format_life_html(life)))

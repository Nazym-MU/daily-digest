#!/usr/bin/env python3
"""Upcoming public Partiful events in and around San Francisco.

Source is partiful.com/explore/sf. Partiful has no public API, but the Explore
page is server-rendered with Next.js and ships its event list as JSON in the
`__NEXT_DATA__` script tag: the trending carousel, curated sections ("Meet New
People!", "Good Eats", "Markets & Fleas"...) and a feed, ~90 events with full
descriptions. Free, no login.

This is the small-gatherings / city-life complement to the Luma section, which is
tech and research. Sports and meet-people events are listed first; everything
else that's public, upcoming and close by fills the rest.

Events already sent are remembered in `.seen_partiful.json`, so each one shows up
once. When nothing new is upcoming, the section is omitted from the digest.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

EXPLORE_URL = "https://partiful.com/explore/sf"
EVENT_URL = "https://partiful.com/e/{}"
# The page is served to browsers; a bot UA risks a different (or blocked) response.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TIMEOUT = 30

HOME_LAT, HOME_LNG = 37.7749, -122.4194
# The "Bay" region reaches Mountain View and beyond; 25 km keeps SF, Oakland, Berkeley.
MAX_DISTANCE_KM = float(os.environ.get("PARTIFUL_MAX_KM", "25"))
WINDOW_DAYS = int(os.environ.get("PARTIFUL_WINDOW_DAYS", "10"))
MAX_EVENTS = int(os.environ.get("PARTIFUL_MAX", "7"))

SEEN_PATH = os.environ.get(
    "PARTIFUL_SEEN_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".seen_partiful.json"),
)
SEEN_LIMIT = 1000

# Matched against title + description. Order is priority: earlier tiers are listed first.
TIERS = [
    ("🏃", re.compile(r"volleyball|soccer|football|futsal|pick-?up (game|sport)|run club|"
                      r"running club|\bsports?\b|tennis|pickleball|badminton|"
                      r"climb|bouldering|\bhike\b|hiking", re.I)),
    ("🍝", re.compile(r"meet new people|make friends|new friends|dinner|supper|potluck|"
                      r"picnic|salon|book club|reading group|writing club|game night|"
                      r"mahjong|board games?|trivia|craft|draw|knit|co-?working", re.I)),
]
SPORTS_RE = TIERS[0][1]
LL_RE = re.compile(r"[?&]sll=(-?\d+\.\d+),(-?\d+\.\d+)")


def _fetch_page():
    req = urllib.request.Request(EXPLORE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        page = resp.read().decode("utf-8")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise ValueError("no __NEXT_DATA__ on the Explore page (layout changed?)")
    return json.loads(m.group(1))["props"]["pageProps"]


def _all_events(props):
    """Every event on the page, deduped by id, wherever it sits."""
    items = list((props.get("trendingSection") or {}).get("items") or [])
    for section in props.get("sections") or []:
        items.extend(section.get("items") or [])
    items.extend(props.get("feedItems") or [])
    events = {}
    for item in items:
        event = item.get("event") if isinstance(item, dict) else None
        if event and event.get("id"):
            events[event["id"]] = event
    return events


def _distance_km(lat, lng):
    lat1, lng1, lat2, lng2 = map(radians, (HOME_LAT, HOME_LNG, lat, lng))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def _place(event):
    """(display name, approximate city, distance km or None)."""
    loc = event.get("locationInfo") or {}
    maps = loc.get("mapsInfo") or {}
    name = loc.get("displayName") or maps.get("name") or ""
    city = maps.get("approximateLocation") or ""
    m = LL_RE.search(maps.get("appleMapsUrl") or "")
    dist = _distance_km(float(m.group(1)), float(m.group(2))) if m else None
    return name, city, dist


def _tier(text):
    for i, (_, rx) in enumerate(TIERS):
        if rx.search(text):
            return i
    return len(TIERS)


def load_seen():
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
        print(f"[partiful] could not persist seen-cache: {e}", file=sys.stderr)


def fetch_partiful(skip_seen=True, now=None):
    """New public events in the next WINDOW_DAYS near SF, sports and social first."""
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(days=WINDOW_DAYS)
    seen = set(load_seen()) if skip_seen else set()
    try:
        events = _all_events(_fetch_page())
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        print(f"[partiful] fetch failed: {e}", file=sys.stderr)
        return []

    picks = []
    for event_id, ev in events.items():
        if event_id in seen or not ev.get("isPublic", True) or ev.get("status") not in (None, "PUBLISHED"):
            continue
        try:
            start = datetime.fromisoformat(ev["startDate"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if not now <= start <= until:
            continue
        place, city, dist = _place(ev)
        if dist is not None and dist > MAX_DISTANCE_KM:
            continue
        text = f"{ev.get('title', '')}\n{ev.get('description') or ''}"
        tier = _tier(text)
        picks.append({
            "id": event_id,
            "name": ev.get("title") or "(untitled)",
            "url": EVENT_URL.format(event_id),
            "start": start,
            "timezone": ev.get("timezone") or "America/Los_Angeles",
            "place": place,
            "city": city,
            "tier": tier,
            "going": ev.get("goingGuestCount") or 0,
        })

    picks.sort(key=lambda e: (e["tier"], e["start"]))
    return picks[:MAX_EVENTS]


def format_partiful_html(events):
    def esc(s):
        return html.escape(str(s))

    lines = ["<b>🎈 Around SF on Partiful</b>"]
    for e in events:
        local = e["start"].astimezone(ZoneInfo(e["timezone"]))
        clock = local.strftime("%-I:%M%p").replace(":00", "").lower()
        when = f'{local.strftime("%a %b %-d")}, {clock}'
        icon = TIERS[e["tier"]][0] if e["tier"] < len(TIERS) else ""
        where = e["place"] or e["city"]
        meta = [when, where, f'{e["going"]} going' if e["going"] else "", icon]
        lines.append(f'• <a href="{esc(e["url"])}">{esc(e["name"])}</a>')
        lines.append(f'  <i>{esc(" · ".join(m for m in meta if m))}</i>')
    return lines


if __name__ == "__main__":
    # Manual check, ignores the seen-cache: python3 partiful.py
    found = fetch_partiful(skip_seen=False)
    print("\n".join(format_partiful_html(found)) if found else "(nothing upcoming)")

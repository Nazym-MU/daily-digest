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
    url = f"{DISCOVER_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_source(source, until):
    """All entries from one feed, stopping once events start past `until`."""
    entries, cursor = [], None
    for _ in range(MAX_PAGES):
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


if __name__ == "__main__":
    # Manual check, ignores the seen-cache: python3 events.py
    found = fetch_events(skip_seen=False)
    print(f"{len(found)} event(s)\n")
    print("\n".join(format_events_html(found)))

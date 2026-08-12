#!/usr/bin/env python3
"""Fetch active tickets from the Obsidian vault published in a public GitHub repo.

The vault's `Tickets/` folder is the single source of truth for the Kanban board on
the 3D site. This module reads the same files over the GitHub API so the daily digest
can surface what is actually open, which is the whole point: the board is easy to
forget about, a Telegram message is not.

Reads these values from the environment:
  TICKETS_REPO    - owner/name of the repo holding the vault (default below)
  TICKETS_PATH    - path to the Tickets folder inside that repo
  TICKETS_STALE_DAYS - age in days past which an open ticket gets flagged (default 14)
  GITHUB_TOKEN    - optional; only raises the API rate limit, not required for a
                    public repo. In Actions this is provided automatically.

Only tickets marked `visibility: public` are ever shown. Private tickets should not
be committed to a public repo in the first place, but this is a second line of
defence rather than a single point of failure.
"""

import os
import sys
import html
import json
import base64
import urllib.error
import urllib.request
from datetime import datetime, timezone

GITHUB_API = "https://api.github.com/repos/{repo}/contents/{path}"

DEFAULT_REPO = "Nazym-MU/NS162"
DEFAULT_PATH = "Downloads/2026/Tickets"
DEFAULT_STALE_DAYS = 14

USER_AGENT = "daily-digest-bot/1.0 (+https://github.com/)"
TIMEOUT = 30

# Only these statuses are "open". `backlog` is deliberately excluded: the vault
# convention is that everything outside the one active milestone sits in backlog,
# so including it would reproduce the forty-item wall the board is designed to avoid.
OPEN_STATUSES = ("doing", "todo")


def _get(url, headers=None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def _auth_headers():
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def parse_frontmatter(text):
    """Parse the ticket's YAML frontmatter.

    Hand-rolled to match the vault's exact schema (`key: value` and `key: [a, b]`),
    mirroring the site's build script. The values here are prose containing colons
    (`done_when` especially), so the split is on the FIRST colon only.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}

    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key] = [v.strip() for v in inner.split(",") if v.strip()]
        else:
            fm[key] = value
    return fm


def _ticket_sort_key(t):
    """Sort `doing` above `todo`, then by numeric id so order is stable day to day."""
    status_rank = OPEN_STATUSES.index(t["status"]) if t["status"] in OPEN_STATUSES else 99
    try:
        num = int(str(t.get("id", "")).split("-")[-1])
    except ValueError:
        num = 0
    return (status_rank, num)


def _age_days(created):
    """Whole days since the ticket's `created` date, or None if unparseable."""
    if not created:
        return None
    try:
        d = datetime.strptime(str(created).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - d).days


def fetch_tickets(repo=None, path=None):
    """Return open, public tickets as a list of dicts, sorted for display.

    Returns [] on any network or API failure. A digest that is missing its ticket
    section is a much smaller problem than a digest that fails to send.
    """
    repo = repo or os.environ.get("TICKETS_REPO", DEFAULT_REPO)
    path = path or os.environ.get("TICKETS_PATH", DEFAULT_PATH)

    url = GITHUB_API.format(repo=repo, path=urllib.parse.quote(path))
    try:
        listing = json.loads(_get(url, headers=_auth_headers()))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[tickets] listing failed: {e}", file=sys.stderr)
        return []

    if not isinstance(listing, dict) and not isinstance(listing, list):
        return []
    if isinstance(listing, dict):
        # An error payload (rate limit, 404) comes back as an object, not an array.
        print(f"[tickets] unexpected response: {listing.get('message')}", file=sys.stderr)
        return []

    tickets = []
    for entry in listing:
        name = entry.get("name", "")
        if entry.get("type") != "file" or not name.endswith(".md"):
            continue

        # The listing includes base64 content for small files; fall back to a
        # second request only when GitHub omits it.
        content = entry.get("content")
        if content and entry.get("encoding") == "base64":
            try:
                raw = base64.b64decode(content).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
        else:
            download_url = entry.get("download_url")
            if not download_url:
                continue
            try:
                raw = _get(download_url, headers=_auth_headers())
            except urllib.error.URLError as e:
                print(f"[tickets] fetch {name} failed: {e}", file=sys.stderr)
                continue

        fm = parse_frontmatter(raw)
        if fm.get("visibility") != "public":
            continue
        if fm.get("status") not in OPEN_STATUSES:
            continue

        tickets.append({
            "id": fm.get("id", "?"),
            "title": fm.get("title", "(untitled)"),
            "project": fm.get("project", ""),
            "milestone": fm.get("milestone", ""),
            "status": fm.get("status", ""),
            "done_when": fm.get("done_when", ""),
            "age_days": _age_days(fm.get("created")),
        })

    tickets.sort(key=_ticket_sort_key)
    return tickets


def format_tickets_html(tickets, stale_days=None, per_project=None):
    """Format the ticket section as Telegram HTML lines.

    Telegram caps a message at 4096 characters for the WHOLE digest, and the full
    board with every `done_when` runs past that on its own. More to the point, a wall
    of 22 tickets is the same thing that made the board easy to ignore. So:
    `done_when` is shown only for what is actually in progress, and each project
    contributes at most `per_project` lines with the remainder counted, not listed.
    """
    if stale_days is None:
        stale_days = int(os.environ.get("TICKETS_STALE_DAYS", DEFAULT_STALE_DAYS))
    if per_project is None:
        per_project = int(os.environ.get("TICKETS_PER_PROJECT", "3"))

    def esc(s):
        return html.escape(str(s))

    lines = ["<b>📋 Open tickets</b>"]
    if not tickets:
        lines.append("• (nothing open, or the vault could not be reached)")
        return lines

    by_project = {}
    for t in tickets:
        by_project.setdefault(t["project"] or "unfiled", []).append(t)

    for project, items in by_project.items():
        milestone = items[0].get("milestone", "")
        header = f"<b>{esc(project)}</b>"
        if milestone:
            header += f" <i>{esc(milestone)}</i>"
        lines.append(header)

        for t in items[:per_project]:
            marker = "▶" if t["status"] == "doing" else "•"
            lines.append(f'{marker} <b>{esc(t["id"])}</b> {esc(t["title"])}')
            # Only the in-progress ticket earns its finish line here. For everything
            # else the title is enough to jog memory, which is all this section is for.
            if t["status"] == "doing" and t["done_when"]:
                lines.append(f'   <i>done when: {esc(t["done_when"])}</i>')

        hidden = len(items) - per_project
        if hidden > 0:
            lines.append(f'   <i>+{hidden} more</i>')
        lines.append("")

    # Staleness is computed from `created`, not from when the ticket entered its
    # current status, because the schema has no transition timestamp. Say so in the
    # wording rather than implying a precision the data does not have.
    stale = [t for t in tickets
             if t["age_days"] is not None and t["age_days"] >= stale_days]
    if stale:
        lines.append(f"<b>⏳ Open since {stale_days}+ days ago</b>")
        for t in stale:
            lines.append(f'• {esc(t["id"])} · {esc(t["age_days"])}d — {esc(t["title"])}')

    # Drop a trailing blank so the section does not end with dead space.
    while lines and lines[-1] == "":
        lines.pop()
    return lines


import urllib.parse  # noqa: E402  (needed by fetch_tickets; kept with the others)


if __name__ == "__main__":
    # Manual check: python3 tickets.py
    got = fetch_tickets()
    print(f"{len(got)} open public ticket(s)\n")
    print("\n".join(format_tickets_html(got)))

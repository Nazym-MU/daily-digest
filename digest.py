#!/usr/bin/env python3
"""Send a daily digest to Telegram: one Read/Talk of the Day (Paul Graham, LessWrong,
or a talk from a few YouTube channels; see reads.py), one heavily cited linear algebra
paper from Semantic Scholar, and new Luma events and jobs (each only when there are some).

Reads these values from the environment:
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID     - your chat with the bot
  S2_API_KEY           - Semantic Scholar key (optional but strongly recommended;
                         the anonymous tier is heavily rate-limited)

Run locally:   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 digest.py
In CI:         values come from GitHub repo secrets (see README).
"""

import os
import sys
import html
import urllib.request
import json
from datetime import datetime, timezone

from events import fetch_events, format_events_html, fetch_life_events, format_life_html, mark_life_seen, mark_seen as mark_events_seen
from partiful import fetch_partiful, format_partiful_html, mark_seen as mark_partiful_seen
from jobs import fetch_jobs, format_jobs_html, mark_seen as mark_jobs_seen
from linalg import fetch_linalg_papers, format_papers_html
from reads import fetch_read, format_read_html, mark_seen as mark_read_seen

USER_AGENT = "daily-digest-bot/1.0 (+https://github.com/)"
TIMEOUT = 30


def _get(url, data=None, headers=None):
    """Minimal HTTP helper using only the stdlib (no pip installs needed in CI)."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def build_message(read, papers=None, events=None, jobs=None, partiful=None, life=None):
    """Format an HTML message for Telegram (parse_mode=HTML)."""
    def esc(s):
        return html.escape(str(s))

    today = datetime.now(timezone.utc).strftime("%A, %B %d")
    lines = [f"<b>📰 Daily Digest — {esc(today)}</b>", ""]

    if read:
        lines.extend(format_read_html(read))
        lines.append("")
    lines.extend(format_papers_html(papers or []))

    # No placeholder line here: a day without new events just has no section.
    if events:
        lines.append("")
        lines.extend(format_events_html(events))

    if life:
        lines.append("")
        lines.extend(format_life_html(life))

    if partiful:
        lines.append("")
        lines.extend(format_partiful_html(partiful))

    if jobs:
        lines.append("")
        lines.extend(format_jobs_html(jobs))

    return "\n".join(lines)


# Telegram rejects any single message over 4096 characters outright.
TELEGRAM_LIMIT = 4096


def split_message(text, limit=TELEGRAM_LIMIT):
    """Split on blank lines so each chunk fits, keeping HTML tags balanced.

    Sections are separated by blank lines and no tag spans one, so splitting there
    never leaves an unclosed <b> or <a>, which would make Telegram reject the chunk.
    """
    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single block over the limit is pathological; hard-cut it rather than
        # dropping it silently.
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks


def send_telegram(token, chat_id, text, buttons=None):
    """`buttons` (an inline keyboard) goes on the last chunk only."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = None
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        fields = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if buttons and i == len(chunks) - 1:
            fields["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        payload = urllib.parse.urlencode(fields).encode("utf-8")
        raw = _get(url, data=payload,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = json.loads(raw)
        if not resp.get("ok"):
            raise RuntimeError(f"Telegram API error: {resp}")
    return resp


def pin_message(token, chat_id, message_id):
    """Pin the paper message silently. listener.py on the laptop reads the pin
    (bots can't read their own messages) to build the morning paper page.
    Best effort: a failed pin must not fail the digest."""
    base = f"https://api.telegram.org/bot{token}"
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        _get(f"{base}/unpinAllChatMessages",
             data=urllib.parse.urlencode({"chat_id": chat_id}).encode(), headers=hdr)
        _get(f"{base}/pinChatMessage", headers=hdr, data=urllib.parse.urlencode({
            "chat_id": chat_id, "message_id": message_id, "disable_notification": "true",
        }).encode())
    except Exception as e:  # noqa: BLE001
        print(f"Pin failed: {e}", file=sys.stderr)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    # Rotates which topics get queried, so a stable high-citation corpus still
    # yields a different slice each day.
    day_index = int(datetime.now(timezone.utc).strftime("%j"))

    read = fetch_read(day_index)
    papers = fetch_linalg_papers(day_index=day_index)
    events = fetch_events()
    life = fetch_life_events(exclude=[e["id"] for e in events])
    partiful = fetch_partiful()
    jobs = fetch_jobs()

    if read is None and not papers:
        print("All sources failed — not sending an empty digest.", file=sys.stderr)
        sys.exit(1)

    message = build_message(read, papers, events, jobs, partiful, life)
    # Tapping this is picked up by listener.py on the laptop, which reads the PDF and
    # replies with a linear-algebra-first summary. callback_data caps at 64 bytes;
    # an S2 paperId is 40 hex chars.
    buttons = [[{"text": "🔍 Deep-dive the paper", "callback_data": f"p:{p['s2_id']}"}]
               for p in (papers or [])[:1] if p.get("s2_id")]
    # Distill: listener.py fetches the essay text / talk transcript and replies with the
    # few claims that matter plus one question for her to answer in her own words.
    distill = [[{"text": "🧭 Distill the " + ("talk" if read["kind"] == "talk" else "read"),
                 "callback_data": f"t:{read['key']}"}]] if read else []
    resp = send_telegram(token, chat_id, message, buttons=(distill + buttons) or None)
    if buttons:
        pin_message(token, chat_id, resp["result"]["message_id"])
    # Only after a successful send, so a failed run doesn't swallow events or jobs.
    mark_read_seen(read)
    mark_events_seen(events)
    mark_life_seen(life)
    mark_partiful_seen(partiful)
    mark_jobs_seen(jobs)
    print("Digest sent.")


# urllib.parse is needed in send_telegram; import here keeps the top tidy.
import urllib.parse  # noqa: E402

if __name__ == "__main__":
    main()

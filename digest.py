#!/usr/bin/env python3
"""Send a daily digest to Telegram: top LessWrong post, Hacker News stories, and
linear algebra papers from arXiv.

Reads these values from the environment:
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID     - your chat with the bot
  HN_COUNT             - how many HN stories to include (optional, default 5)
  S2_API_KEY           - Semantic Scholar key (optional but strongly recommended;
                         the anonymous tier is heavily rate-limited)

Run locally:   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 digest.py
In CI:         values come from GitHub repo secrets (see README).
"""

import os
import sys
import html
import urllib.request
import urllib.error
import json
from datetime import datetime, timedelta, timezone

from linalg import fetch_linalg_papers, format_papers_html

LESSWRONG_GRAPHQL = "https://www.lesswrong.com/graphql"
HN_TOPSTORIES = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"

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


def fetch_top_lesswrong_post(hours=24):
    """Return the highest-karma LessWrong post published in the last `hours`.

    Falls back to the single most recent post if nothing falls inside the window
    (e.g. a quiet day). Returns a dict {title, url, karma, author} or None.
    """
    after = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    # Sort by top within a recent window; grab a handful so we can still pick
    # something sensible if the window is sparse.
    query = """
    query RecentTop($after: String) {
      posts(input: {terms: {view: "top", after: $after, limit: 20}}) {
        results {
          title
          pageUrl
          baseScore
          postedAt
          user { displayName }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"after": after}}).encode("utf-8")
    try:
        raw = _get(LESSWRONG_GRAPHQL, data=payload,
                   headers={"Content-Type": "application/json"})
        results = json.loads(raw)["data"]["posts"]["results"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        print(f"[lesswrong] fetch failed: {e}", file=sys.stderr)
        return None

    if not results:
        # Quiet day inside the window — fall back to the most recent posts overall.
        results = _fetch_recent_lesswrong_fallback()
        if not results:
            return None

    top = max(results, key=lambda p: p.get("baseScore") or 0)
    return {
        "title": top.get("title") or "(untitled)",
        "url": top.get("pageUrl") or "https://www.lesswrong.com",
        "karma": top.get("baseScore") or 0,
        "author": (top.get("user") or {}).get("displayName") or "unknown",
    }


def _fetch_recent_lesswrong_fallback():
    query = """
    query Recent {
      posts(input: {terms: {view: "new", limit: 10}}) {
        results { title pageUrl baseScore postedAt user { displayName } }
      }
    }
    """
    payload = json.dumps({"query": query}).encode("utf-8")
    try:
        raw = _get(LESSWRONG_GRAPHQL, data=payload,
                   headers={"Content-Type": "application/json"})
        return json.loads(raw)["data"]["posts"]["results"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        print(f"[lesswrong] fallback failed: {e}", file=sys.stderr)
        return []


def fetch_top_hn(count=5):
    """Return the top `count` Hacker News stories as [{title, url, hn_url, score}]."""
    try:
        ids = json.loads(_get(HN_TOPSTORIES))[:count]
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[hn] topstories failed: {e}", file=sys.stderr)
        return []

    stories = []
    for item_id in ids:
        try:
            item = json.loads(_get(HN_ITEM.format(item_id)))
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[hn] item {item_id} failed: {e}", file=sys.stderr)
            continue
        if not item:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={item_id}"
        stories.append({
            "title": item.get("title") or "(untitled)",
            # Ask HN / text posts have no url — point at the HN discussion.
            "url": item.get("url") or hn_url,
            "hn_url": hn_url,
            "score": item.get("score") or 0,
        })
    return stories


def build_message(lw, hn, papers=None):
    """Format an HTML message for Telegram (parse_mode=HTML)."""
    def esc(s):
        return html.escape(str(s))

    today = datetime.now(timezone.utc).strftime("%A, %B %d")
    lines = [f"<b>📰 Daily Digest — {esc(today)}</b>", ""]

    lines.append("<b>🧠 Top of LessWrong</b>")
    if lw:
        lines.append(
            f'• <a href="{esc(lw["url"])}">{esc(lw["title"])}</a>\n'
            f'  <i>{esc(lw["karma"])} karma · {esc(lw["author"])}</i>'
        )
    else:
        lines.append("• (couldn't fetch a post today)")
    lines.append("")

    lines.append("<b>🍊 Hacker News</b>")
    if hn:
        for s in hn:
            lines.append(
                f'• <a href="{esc(s["url"])}">{esc(s["title"])}</a> '
                f'(<a href="{esc(s["hn_url"])}">{esc(s["score"])}▲</a>)'
            )
    else:
        lines.append("• (couldn't fetch stories today)")

    lines.append("")
    lines.extend(format_papers_html(papers or []))

    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    raw = _get(url, data=payload,
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = json.loads(raw)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram API error: {resp}")
    return resp


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    hn_count = int(os.environ.get("HN_COUNT", "5"))
    # Rotates which topics get queried, so a stable high-citation corpus still
    # yields a different slice each day.
    day_index = int(datetime.now(timezone.utc).strftime("%j"))

    lw = fetch_top_lesswrong_post()
    hn = fetch_top_hn(hn_count)
    papers = fetch_linalg_papers(day_index=day_index)

    if lw is None and not hn and not papers:
        print("All sources failed — not sending an empty digest.", file=sys.stderr)
        sys.exit(1)

    message = build_message(lw, hn, papers)
    send_telegram(token, chat_id, message)
    print("Digest sent.")


# urllib.parse is needed in send_telegram; import here keeps the top tidy.
import urllib.parse  # noqa: E402

if __name__ == "__main__":
    main()

# Daily Digest Bot

Sends you a Telegram message every morning with:

- 🧠 The **top LessWrong post** of the last 24 hours (highest karma)
- 🍊 The **top 5 Hacker News** stories right now
- 📄 **One paper of the day**: a heavily cited, peer-reviewed linear algebra paper with a
  one-line summary, rotating through five topic buckets (numerical, ML, graphics,
  quantum, theory)
- 📍 **New Bay Area events on Luma** matching my interests (3D, world models, math,
  AI for science, reading groups), but only on days when there are new ones
- 🧮 **New intern / new-grad jobs** on the SWE × math boundary (geometry, simulation,
  graphics, GNC, optimization, formal methods, crypto), from company job boards and
  HN "Who is hiring?", also only when there are new ones

Runs on **GitHub Actions** (free, always-on — no laptop or server needed). Pure Python
standard library, so there's nothing to `pip install`.

## Requirements

- **Python 3.9+** (uses only the standard library — no dependencies)
- A free **Telegram** account and a **GitHub** account

---

## One-time setup (~10 minutes)

### 1. Create your Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, pick a name and username.
3. BotFather replies with a **bot token** like `123456:ABC-DEF...`. Keep it secret.

### 2. Get your chat ID

1. Send any message (e.g. "hi") to **your new bot** in Telegram.
2. Run this, pasting in your token:
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool
   ```
3. Find `"chat": { "id": ... }` in the output. That number is your **chat ID**.

### 3. Test it locally (optional but recommended)

Copy the example env file, fill in your values, then run:

```bash
cp .env.example .env
# edit .env with your real token + chat ID
set -a && source .env && set +a && python3 digest.py
```
You should get the digest in Telegram within a second. (`.env` is gitignored, so your
token never gets committed.)

### 4. Push to a GitHub repo

```bash
git init && git add . && git commit -m "Daily digest bot"
gh repo create daily-digest-bot --private --source=. --push
```
(or create the repo in the GitHub UI and push the usual way.)

### 5. Add your secrets to the repo

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Name                  | Value             | Required?                          |
|-----------------------|-------------------|------------------------------------|
| `TELEGRAM_BOT_TOKEN`  | your bot token    | yes                                |
| `TELEGRAM_CHAT_ID`    | your chat ID      | yes                                |
| `S2_API_KEY`          | Semantic Scholar key | no, but strongly recommended — see [Rate limits](#rate-limits--read-this) |

### 6. Verify

Go to the **Actions** tab → **Daily Digest** → **Run workflow** to fire it manually
once. If the message lands, you're done — it'll now run every day automatically.

---

## About the schedule

The workflow runs at `17 11 * * *` — **11:17 UTC**, which is 4:17 AM Pacific Daylight
Time (3:17 AM in winter; GitHub cron has no daylight saving).

That's deliberately early. **GitHub delays scheduled runs**, and not by minutes: when
the cron was `0 13 * * *` (6 AM), runs in October 2026 started anywhere from 16:00 to
19:40 UTC, so the digest arrived mid-morning. Starting early and off the top of the
hour (when the queue is busiest) means it is waiting when you wake up. If you ever
need exact timing, a tiny cloud VM with real cron is the alternative.

## The linear algebra section

Papers come from the **Semantic Scholar API**. Everything lives in `linalg.py`.

### Why not arXiv

The obvious source is arXiv's daily feed, and this bot used it first. The problem is
that arXiv is a *preprint* server: nothing is peer-reviewed at submission time. A
measured sample of 100 recent `cs.LG` papers found:

| Signal | Coverage |
|--------|----------|
| `journal_ref` (published venue) | 5% |
| `doi` | 3% |
| Any venue-acceptance mention | 18% (mostly *workshops*, not main conferences) |

There is essentially no quality signal on a paper posted yesterday, because citations
take 1–2 years to accumulate. **Fresh and vetted are close to mutually exclusive.**

Semantic Scholar exposes what arXiv can't: real citation counts and peer-reviewed
venue names. The tradeoff is deliberate — you get work that other researchers have
actually read and built on, at the cost of it not being from this week.

### What gets through

The digest sends **one paper a day**, not a list. Every candidate must clear a high
per-bucket citation floor **and** have a real peer-reviewed venue (anything whose
venue is `arXiv.org` is dropped as an unvetted preprint). The floor is also passed to
the search as `minCitationCount`, so every result is a candidate.

| Bucket | Min citations | Example of what lands here |
|--------|---------------|----------------------------|
| `numerical` | 200 | *Randomized numerical linear algebra: Foundations and algorithms* — 528 cit, Acta Numerica |
| `ml` | 1000 | *LoRA: Low-Rank Adaptation of LLMs* — 21833 cit, ICLR |
| `graphics` | 300 | *3D Gaussian Splatting for Real-Time Radiance Fields* — 9769 cit, ACM TOG |
| `quantum` | 150 | Quantum linear systems, QSVT, block encodings |
| `theory` | 200 | Matrix analysis, spectral graph theory, random matrix theory |

The day-of-year picks the bucket, and the most-cited unseen paper in it is sent. If that
bucket has nothing above its floor, the most-cited qualifying paper from any bucket
stands in. Semantic Scholar's one-sentence TLDR is added underneath when it has one.

Floors differ per bucket because quantum LA and graphics are smaller fields that cite
less in absolute terms — a flat threshold would quietly starve them.

### Keeping it varied

A high-citation corpus is stable, so querying the same topics daily would surface the
same papers forever. Two mechanisms prevent that:

1. **Rotation**: buckets take turns day by day, and each bucket holds several queries;
   the day-of-year picks which one runs.
2. **Seen-cache** (`.seen_papers.json`) — papers already sent are skipped, and dropped
   from the pool so it advances to deeper cuts.

### Rate limits — read this

Semantic Scholar's **anonymous tier is a shared global bucket** (~1 request/sec across
*all* unauthenticated clients worldwide). In testing, even a single request after a
four-minute cooldown returned HTTP 429. It is not reliable for scheduled runs.

Two things address this:

- **Get a free API key** (strongly recommended):
  [semanticscholar.org/product/api#api-key](https://www.semanticscholar.org/product/api#api-key).
  Add it as the repo secret `S2_API_KEY`. This gives you a private quota and makes the
  problem go away.
- **The paper pool** (`.paper_pool.json`) — a reservoir of vetted papers, refilled
  whenever the API does answer. On a rate-limited morning the paper still comes from
  the bank. The GitHub Actions cache step persists it between runs.

Without a key the bot still works, but the pool takes several successful runs to fill,
so expect thin digests for the first few days.

### Tuning

- **Topics:** edit `TOPICS` in `linalg.py` — plain search strings, easy to extend.
- **Stricter or looser:** `MIN_CITATIONS`. Lowering it widens the pool but lets in
  less established work.
- **Different era:** `LINALG_YEAR_RANGE` (default `2015-2026`).

Preview without sending to Telegram (optionally pass a day index):

```bash
python3 linalg.py       # today's rotation
python3 linalg.py 5     # what day 5 of the rotation would pick
```

## The Luma events section

Lives in `events.py`. Events come from Luma's public **discover** feed
(`api.lu.ma/discover/get-paginated-events`), from the AI, Tech, and San Francisco
feeds, pinned to SF coordinates. Luma's search endpoint needs a signed-in session
and the official API only covers calendars you manage, so neither works from CI.

- **Matching** is a keyword pass over the event name and host calendar name, since
  the feed has no descriptions. Edit `TOPICS` to change it. Generic mixers and happy
  hours are left out on purpose, because two weeks of the feed had ~90 of them.
- **Filters:** in-person, within `EVENTS_MAX_KM` (70) of SF, starting in the next
  `EVENTS_WINDOW_DAYS` (14). At most `EVENTS_MAX` (6) per digest, soonest first.
  Matches that don't fit roll over to the next day.
- **Only new events:** sent ids go in `.seen_events.json`, cached between runs.
  They're recorded only after Telegram accepts the message. With nothing new, the
  section is omitted entirely.
- **Rate limits:** heavy polling gets a 403 `additional-verification-required`.
  One run makes ~10 requests, which is fine, and a 403 just means no section that day.

Preview without sending (ignores the seen-cache):

```bash
python3 events.py
```

## The jobs section

Lives in `jobs.py`. LinkedIn has no public jobs API (and scraping it breaks its terms),
so jobs come straight from company boards: most companies post through Greenhouse,
Lever or Ashby, which all serve an anonymous JSON feed. The watchlist is `COMPANIES`
— about 60 companies in CAD/simulation, formal math, ZK, graphics, autonomy,
aerospace, quantum and AI-for-science. Each entry is `(name, ats, slug, core)`;
the slug is the part after the board's domain, e.g. `jobs.ashbyhq.com/<slug>`.

A posting is kept when:

- the **title** says intern / co-op / new grad / entry level / junior (or Ashby tags
  it Intern), and nothing senior, PhD, master's or MBA;
- it's an engineering or research title, not sales, ops, hardware build or firmware;
- it's **mathy**: at broad companies the title must name the math (GNC, simulation,
  computational, controls, kernel, 3D, research…), and the title or description must
  hit a `TOPICS` entry. Companies marked `core` are math-native, so any technical
  early-career role there counts;
- the description doesn't **require a PhD or master's**;
- it's in the **US** (or has no location). "Remote" alone doesn't count, since it
  often means remote within Europe.

Roles posted once per season or per city are collapsed into one line. Bay Area roles
come first, then newest. At most `JOBS_MAX` (8) board jobs plus `JOBS_MAX_HN` (3) HN
posts per digest; the rest roll over to the next day.

The HN source is the latest monthly "Who is hiring?" thread (via the Algolia API).
A comment is kept when it mentions interns / new grads / junior roles, at least two
distinct topic terms, isn't trading, and its header line isn't outside the US.

Sent ids go in `.seen_jobs.json`, cached between runs like the events cache, so each
job appears once. A board that fails just logs and is skipped.

Preview without sending (ignores the seen-cache; raise the caps to see everything):

```bash
JOBS_MAX=200 JOBS_MAX_HN=50 python3 jobs.py
```

## Customizing

- **More/fewer HN stories:** change `HN_COUNT` in `.github/workflows/digest.yml`.
- **Different LessWrong pick:** edit `fetch_top_lesswrong_post` in `digest.py` (the
  GraphQL `view` term — `"top"`, `"new"`, `"curated"` — controls the selection).
- **Different time:** edit the `cron` line. Format is `minute hour day month weekday`, UTC.

## License

MIT — see [LICENSE](LICENSE). Fork it, change it, ship it.

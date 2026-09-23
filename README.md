# Daily Digest Bot

Sends you a Telegram message every morning with:

- 🧠 The **top LessWrong post** of the last 24 hours (highest karma)
- 🍊 The **top 5 Hacker News** stories right now
- 🔢 High-citation, peer-reviewed **linear algebra papers** across five topic buckets
  (numerical, ML, graphics, quantum, theory)

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

The workflow runs at `0 13 * * *` — **13:00 UTC**, which is **6:00 AM Pacific Daylight
Time**. Two things to know:

- **GitHub cron has no daylight saving.** During Pacific *Standard* Time (roughly
  November–March) 13:00 UTC is **5:00 AM** Pacific. If you want a steady 6:00 AM
  year-round, change the cron to `0 14 * * *` for the winter months, or just accept the
  one-hour seasonal drift.
- **GitHub may delay scheduled runs** by a few minutes (sometimes more) during peak
  load. For a morning digest that's harmless. If you ever need exact timing, a tiny
  cloud VM with real cron is the alternative.

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

Every paper must clear a per-bucket citation floor **and** have a real peer-reviewed
venue (anything whose venue is `arXiv.org` is dropped as an unvetted preprint).

| Bucket | Slots | Min citations | Example of what lands here |
|--------|-------|---------------|----------------------------|
| `numerical` | 2 | 25 | *Randomized numerical linear algebra: Foundations and algorithms* — 528 cit, Acta Numerica |
| `ml` | 1 | 40 | *LoRA: Low-Rank Adaptation of LLMs* — 21833 cit, ICLR |
| `graphics` | 1 | 20 | *3D Gaussian Splatting for Real-Time Radiance Fields* — 9769 cit, ACM TOG |
| `quantum` | 1 | 15 | *Hybrid quantum linear equation algorithm* — 82 cit, Scientific Reports |
| `theory` | 1 | 20 | Matrix analysis, spectral graph theory, random matrix theory |

Floors differ per bucket because quantum LA and graphics are smaller fields that cite
less in absolute terms — a flat threshold would quietly starve them.

### Keeping it varied

A high-citation corpus is stable, so querying the same topics daily would surface the
same papers forever. Two mechanisms prevent that:

1. **Topic rotation** — each bucket holds several queries; the day-of-year picks which
   one runs, so Monday and Tuesday explore different slices.
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
  whenever the API does answer. A rate-limited morning still sends a full digest from
  the bank. The GitHub Actions cache step persists it between runs.

Without a key the bot still works, but the pool takes several successful runs to fill,
so expect thin digests for the first few days.

### Tuning

- **Topics:** edit `TOPICS` in `linalg.py` — plain search strings, easy to extend.
- **Stricter:** raise `MIN_CITATIONS`. Setting `numerical` to 100+ gets you only
  landmark papers.
- **Different era:** `LINALG_YEAR_RANGE` (default `2015-2026`).
- **More per bucket:** `BUCKET_SLOTS`.

Preview without sending to Telegram (optionally pass a day index):

```bash
python3 linalg.py       # today's rotation
python3 linalg.py 5     # what day 5 of the rotation would pick
```

## Customizing

- **More/fewer HN stories:** change `HN_COUNT` in `.github/workflows/digest.yml`.
- **Different LessWrong pick:** edit `fetch_top_lesswrong_post` in `digest.py` (the
  GraphQL `view` term — `"top"`, `"new"`, `"curated"` — controls the selection).
- **Different time:** edit the `cron` line. Format is `minute hour day month weekday`, UTC.

## License

MIT — see [LICENSE](LICENSE). Fork it, change it, ship it.

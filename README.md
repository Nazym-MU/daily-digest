# Daily Digest Bot

Sends you a Telegram message every morning with:

- 🧠 The **top LessWrong post** of the last 24 hours (highest karma)
- 🍊 The **top 5 Hacker News** stories right now

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
Add two:

| Name                  | Value             |
|-----------------------|-------------------|
| `TELEGRAM_BOT_TOKEN`  | your bot token    |
| `TELEGRAM_CHAT_ID`    | your chat ID      |

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

## Customizing

- **More/fewer HN stories:** change `HN_COUNT` in `.github/workflows/digest.yml`.
- **Different LessWrong pick:** edit `fetch_top_lesswrong_post` in `digest.py` (the
  GraphQL `view` term — `"top"`, `"new"`, `"curated"` — controls the selection).
- **Different time:** edit the `cron` line. Format is `minute hour day month weekday`, UTC.

## License

MIT — see [LICENSE](LICENSE). Fork it, change it, ship it.

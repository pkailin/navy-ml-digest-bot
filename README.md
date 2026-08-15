# Navy & ML Daily Digest Bot

Fully serverless daily Telegram digest covering:
- 🚢 Naval/navy news (Naval News, USNI News, The War Zone, plus targeted searches for
  USVs, Strait of Hormuz, navy exercises)
- ⚔️ War & conflict tech (ISW analysis, Russia-Ukraine drone/USV advancements like
  Magura and Sea Baby, Black Sea drone warfare, Houthi/Red Sea attacks, battlefield autonomy)
- 🤖 ML advancements for autonomous vehicles (air/land/sea) and NVIDIA news

It runs once a day (default 08:00 SGT) on **GitHub Actions' free tier** — no phone, laptop,
or server needs to stay on. GitHub's cloud runner wakes up, checks the feeds, and pushes
new items straight to your Telegram chat.

## One-time setup (~10 minutes)

### 1. Create the Telegram bot
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
2. Copy the token it gives you (looks like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).
3. Send your new bot any message (e.g. "hi") so it knows who you are.
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find
   `"chat":{"id": NNNNNNN, ...}` — that number is your chat ID.

### 2. Create a GitHub repo for this bot
1. Go to github.com → New repository (can be private) → e.g. `navy-ml-digest-bot`.
2. Upload all the files in this folder to the repo (drag-and-drop on the GitHub web UI
   works fine, or `git push` if you're comfortable with git).

### 3. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_BOT_TOKEN` = the token from BotFather
- `TELEGRAM_CHAT_ID` = your chat ID

### 4. Enable Actions and test it
1. Go to the **Actions** tab in your repo → enable workflows if prompted.
2. Click **Daily Navy & ML Digest** → **Run workflow** to trigger it manually and confirm
   you get a Telegram message.
3. After that it runs automatically every day at 00:00 UTC (08:00 SGT).

## Getting the digest on demand (not just at 8am)

**Easiest — no extra setup:** open the GitHub app on your phone → your repo → **Actions**
tab → *Daily Navy & ML Digest* → **Run workflow**. It'll message you within a minute.

**Nicer — just text your bot:** the `webhook/` folder contains a small Cloudflare Worker
(free tier, no credit card needed) that listens for Telegram messages and triggers the
same GitHub Action whenever you send `/news` to your bot.

Setup (~10 min, one time):
1. Sign up free at [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages**
   → **Create Worker**.
2. Paste the contents of `webhook/worker.js` into the editor and deploy.
3. In the worker's **Settings → Variables → Encrypt**, add these secrets:
   - `TELEGRAM_BOT_TOKEN` — from BotFather
   - `TELEGRAM_CHAT_ID` — your chat ID
   - `GITHUB_OWNER` — your GitHub username
   - `GITHUB_REPO` — this repo's name
   - `GITHUB_PAT` — a GitHub personal access token: go to
     github.com/settings/tokens → **Fine-grained token** → give it **Actions: Read and
     write** permission, scoped to this repo only
4. Copy your worker's URL (looks like `https://navy-ml-digest-webhook.<you>.workers.dev`).
5. Point Telegram at it by visiting this URL once in a browser (fill in your own token
   and worker URL):
   `https://api.telegram.org/bot<YOUR_TELEGRAM_TOKEN>/setWebhook?url=<YOUR_WORKER_URL>`

From then on, texting `/news` to your bot triggers a fresh run immediately — no need to
open GitHub at all.

## Customizing

- **Change the schedule**: edit the `cron` line in
  `.github/workflows/daily-digest.yml` (cron is in UTC).
- **Add/remove sources**: edit the `NAVY_FEEDS`, `WAR_FEEDS`, and `ML_FEEDS` lists at
  the top of `main.py`. Any RSS feed URL works; Google News search feeds are built with
  the `gnews("your search terms")` helper, so you can add new topics without any API key.
- **Lookback window**: `LOOKBACK_HOURS` in `main.py` (default 30h, so a slightly late
  run never misses a day).

## How dedup works

`state.json` stores links already sent so you never get the same story twice. The
workflow commits the updated file back to the repo after each run — this is why the
workflow needs `contents: write` permission.

## Cost

$0. GitHub Actions gives generous free minutes for scheduled jobs like this
(a run here takes well under a minute), and the Telegram Bot API is free.

# PulseBrief

A local-first personal **news intelligence agent**. PulseBrief fetches news for topics you choose, triages and ranks it, writes rich AI summaries, groups related coverage into story clusters, and delivers a daily intelligence brief to your phone — learning from what you save and ignore over time.

## Features

- **Multi-source ingestion** — NewsAPI + GDELT (supplement) + RSS feeds + Hacker News via modular connectors in `sources.yaml`
- **Local-first pipeline** — fetch 100–300 articles → normalize → dedupe (rapidfuzz) → score → TF-IDF cluster → rank clusters → extract text for finalists only → **ONE batched Groq call** for the full brief
- **Groq budget manager** — daily request/token caps; `explain`/`compare` only on demand
- **Story clustering** — related articles grouped with shared source links and conflict flags
- **Personalized memory** — `save`/`ignore` interactions (SQLite) plus git-portable mutes/preferences (`preferences.yaml`) that also apply to your cloud runs
- **Daily intelligence brief** — Top Stories → per-topic sections → Watchlist
- **AI summarization** via Groq (free) or OpenAI, with an extractive fallback and a failure queue
- **Pluggable delivery** — ntfy (free push, with priority + action button), Twilio (SMS/WhatsApp), Slack, or console
- **Two-way + local commands** — `more`, `full`, `explain`, `today`, `topics`, `history`, `save`, `ignore`
- **REST API** via FastAPI; **recurring scheduling** locally (APScheduler) or in the cloud (GitHub Actions)

## Requirements

- Python 3.11+
- Optional API keys: OpenAI, NewsAPI, and a delivery channel (Twilio or Slack)

## Setup

1. **Clone or enter the project directory:**

   ```bash
   cd pulsebrief
   ```

2. **Create a virtual environment (recommended):**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add your keys as needed:

   | Variable | Description |
   |----------|-------------|
   | `GROQ_API_KEY` | Groq key for free AI summaries (used first when set). Get one at [console.groq.com/keys](https://console.groq.com/keys) |
   | `GROQ_MODEL` | Cheap model for the single batched daily brief (`llama-3.1-8b-instant`) |
   | `GROQ_DEEP_MODEL` | Stronger model for on-demand `explain`/`compare` only |
   | `GROQ_MAX_DAILY_REQUESTS` | Hard cap on Groq API calls per day (default 20) |
   | `GROQ_MAX_TOKENS_PER_DIGEST` | Max tokens for the batched brief request (default 6000) |
   | `OPENAI_API_KEY` | OpenAI key, used only if no Groq key (extractive fallback if empty/out of quota) |
   | `NEWS_API_KEY` | NewsAPI key (optional — uses GDELT if empty; a free key from [newsapi.org](https://newsapi.org) is recommended for reliable fetching) |
   | `DELIVERY_CHANNEL` | `ntfy` (default, free), `twilio`, `slack`, or `console` |
   | `NTFY_TOPIC` | Unique ntfy topic to publish to (subscribe to it in the ntfy app) |
   | `NTFY_SERVER` | ntfy server (default `https://ntfy.sh`) |
   | `NTFY_TOKEN` | Optional ntfy auth token (for reserved/protected topics) |
   | `TWILIO_ACCOUNT_SID` | Twilio Account SID |
   | `TWILIO_AUTH_TOKEN` | Twilio Auth Token |
   | `TWILIO_FROM_NUMBER` | Sender number. SMS: `+1...`; WhatsApp: `whatsapp:+...` |
   | `TWILIO_TO_NUMBER` | Recipient number (your phone). Match SMS/WhatsApp format of the sender |
   | `SLACK_BOT_TOKEN` | Slack bot token (only if `DELIVERY_CHANNEL=slack`) |
   | `SLACK_CHANNEL_ID` | Channel ID to post digests (Slack only) |
   | `DIGEST_INTERVAL_HOURS` | Run the digest every N hours (default: 6) |
   | `RUN_ON_STARTUP` | If `true`, run a digest ~10s after the server starts |
   | `TIMEZONE` | IANA timezone, e.g. `America/Los_Angeles` |
   | `MAX_ARTICLES_PER_TOPIC` | Max stories per topic in the final brief (default: 3) |
   | `MAX_TOTAL_ARTICLES` | Max total candidates considered across all topics (default: 40) |
   | `MAX_PER_SOURCE` | Max stories any single outlet can contribute (default: 2) |
   | `CANDIDATES_PER_TOPIC` | Candidates pulled per topic before AI triage (default: 6) |
   | `MIN_IMPORTANCE` | Drop anything the AI scores below this importance, 1–10 (default: 7) |

5. **Customize** `topics.yaml`, `sources.yaml`, `config.yaml`, and `preferences.yaml`.

## Configuration files

| File | Purpose |
|------|---------|
| `topics.yaml` | Topics, keywords, priority, max clusters, source preferences |
| `sources.yaml` | RSS feeds, NewsAPI, GDELT, HN connectors (add/remove freely) |
| `config.yaml` | Scoring weights, clustering thresholds, fetch limits, Groq/ntfy tuning |
| `preferences.yaml` | Muted keywords/sources (commit to apply to cloud runs) |

## Groq free-tier strategy

PulseBrief processes **hundreds of articles locally** but sends **one compact batched request** to Groq for the final intelligence brief:

1. Fetch up to 300 articles (NewsAPI + RSS + GDELT supplement + HN)
2. Normalize, dedupe, score, and TF-IDF cluster **without any LLM**
3. Select top 4–6 story clusters
4. Extract full article text **only for finalists** (trafilatura)
5. Compress each cluster to ~350 tokens of context
6. **One Groq call** produces the full Morning Brief JSON
7. `explain`, `compare`, `more`, `full` use **cached data**; Groq only if you request deeper analysis

Daily caps (`GROQ_MAX_DAILY_REQUESTS=20`) prevent runaway usage. If Groq fails or is over budget, a local extractive fallback still delivers a digest.

## Setting up ntfy (free default delivery)

1. Install the **ntfy** app on your phone ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. In `.env`, set a unique, hard-to-guess `NTFY_TOPIC` (anyone who knows the topic can read it).
3. In the app, tap **+** and subscribe to that exact topic name (server `ntfy.sh`).
4. Run a digest — it arrives as a push notification. No account or payment needed.

## Setting up Twilio (paid SMS/WhatsApp delivery)

1. Create a free account at [twilio.com/try-twilio](https://www.twilio.com/try-twilio).
2. From the [Twilio Console](https://console.twilio.com) dashboard, copy your **Account SID** and **Auth Token** → set as `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`.
3. Choose **SMS** or **WhatsApp**:
   - **SMS:** Buy a phone number (Console → Phone Numbers → Buy a number, with SMS capability). Set `TWILIO_FROM_NUMBER=+1XXXXXXXXXX` and `TWILIO_TO_NUMBER=+1<your cell>`. On a trial account you must first verify your personal number under **Verified Caller IDs**.
   - **WhatsApp:** Use the [WhatsApp Sandbox](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn) (Messaging → Try it out → WhatsApp). Join the sandbox by texting the given code, then set `TWILIO_FROM_NUMBER=whatsapp:+14155238886` (the sandbox number) and `TWILIO_TO_NUMBER=whatsapp:+1<your cell>`.
4. (Optional, for two-way replies) Expose your local server with a tunnel (e.g. `ngrok http 8000`) and set the number's **messaging webhook** to `https://<public-host>/twilio/sms`. Then you can reply `more 1`, `full 2`, `topics`, or `run digest` and PulseBrief responds.

Long digests are automatically split into multiple messages to respect Twilio's per-message length limit.

## Creating a Slack Bot (alternative delivery)

Set `DELIVERY_CHANNEL=slack`, then:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app.
2. Under **OAuth & Permissions**, add bot scopes:
   - `chat:write`
   - `channels:read` (if posting to public channels)
3. Install the app to your workspace and copy the **Bot User OAuth Token** → set as `SLACK_BOT_TOKEN`.
4. Invite the bot to your target channel: `/invite @YourBotName`
5. Get the channel ID (right-click channel → View channel details → copy ID) → set as `SLACK_CHANNEL_ID`.
6. (Optional) Add a Slash Command or Event Subscription pointing to `http://your-server/slack/events` for interactive replies.

## Running Locally

**Start the API server (includes daily scheduler):**

```bash
uvicorn app.main:app --reload
```

The server runs at `http://127.0.0.1:8000`. API docs at `/docs`.

**Run a digest manually via CLI:**

```bash
python cli.py run                 # fetch, summarize, cluster, deliver
python cli.py run --no-send       # build the brief but print locally instead of sending
```

**Read & explore:**

```bash
python cli.py today               # print the latest Morning Brief
python cli.py topics              # list configured topics
python cli.py history             # recent digest runs
python cli.py more 1              # longer summary for story #1
python cli.py full 1              # full brief (background, entities, bias) for #1
python cli.py explain 1           # deep dive (Groq on demand)
python cli.py compare 1           # how sources frame the same story (Groq on demand)
python cli.py sources 1           # all source links for story #1
python cli.py stats             # pipeline + Groq usage stats
```

**Teach it your preferences (memory):**

```bash
python cli.py save 1              # remember you liked story #1 (boosts its topic later)
python cli.py ignore 2            # down-rank story #2 and its source going forward
python cli.py mute-keyword "celebrity gossip"   # never show stories matching this
python cli.py mute-source "Biztoc.com"          # never show stories from this outlet
python cli.py add-topic "Climate"               # add a topic to topics.yaml
python cli.py remove-topic "Sports"             # remove a topic
```

> Mutes and topic edits are written to `preferences.yaml` / `topics.yaml`. Because your scheduled digest runs on an **ephemeral GitHub Actions runner** (no access to your local database), these files are how preferences reach the cloud — **commit and push them** to apply there. `save`/`ignore` history lives in your local SQLite and shapes local runs.

## Always-on Cloud Scheduling (GitHub Actions)

`.github/workflows/digest.yml` runs the digest in the cloud so it works even with your laptop closed. Instead of fixed clock times, it implements **"every `MIN_INTERVAL_HOURS` since the last real run"**: a cron wakes hourly, but a guard step skips immediately unless enough time has passed since the last actual run (the timestamp is persisted in the Actions cache). This makes a manual run reset the timer and is robust to GitHub's best-effort scheduling delays.

Set repository **Secrets** (`Settings → Secrets and variables → Actions`): `GROQ_API_KEY`, `NEWS_API_KEY`, `NTFY_TOPIC`. Non-secret tuning (model, limits, channel) lives as `env:` in the workflow. Commit `preferences.yaml`/`topics.yaml` to control what the cloud run delivers.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/topics` | List configured topics |
| POST | `/digest/run` | Run digest now |
| GET | `/digest/history` | Recent digest runs |
| GET | `/clusters/latest` | Story clusters from the latest digest |
| GET | `/articles/recent` | Recent stored articles |
| GET | `/articles/{id}` | Article detail |
| GET | `/articles/{id}/long-summary` | Long summary only |
| POST | `/twilio/sms` | Twilio SMS/WhatsApp inbound command handler (TwiML reply) |
| POST | `/slack/events` | Slack command handler |

Example:

```bash
curl -X POST http://127.0.0.1:8000/digest/run
curl http://127.0.0.1:8000/articles/recent
```

## Customizing Topics

Edit `topics.yaml`. Each topic has a name, keywords (for relevance scoring), and queries (for news search):

```yaml
topics:
  - name: AI
    keywords:
      - artificial intelligence
      - machine learning
    queries:
      - "artificial intelligence" OR "machine learning"
```

Restart the server after changes (topics are loaded on each digest run).

## How It Works

1. **Fetch** — For each topic, query NewsAPI or GDELT for recent English articles (with retry + rate-limit handling).
2. **Deduplicate** — Drop duplicates by canonical URL (tracking params stripped), similar titles, and similar descriptions.
3. **Filter + rank** — Remove muted keywords/sources; score by recency, relevance, reputation, diversity, and your saved/ignored history. Hard caps per source and per topic.
4. **Triage (cheap)** — One batched AI call scores every candidate's importance (1–10) and category. Anything below `MIN_IMPORTANCE` or with no clear category is dropped.
5. **Summarize (rich)** — Only the finalists get a full structured summary (`tldr`, `why_it_matters`, `bias_or_angle`, `key_entities`, `background`, `what_changed_today`, `what_to_watch_next`, …). Failures go to a retry queue and fall back to extractive summaries.
6. **Cluster** — Group related stories, attach 2–4 source links, flag multi-outlet coverage.
7. **Store** — Persist articles, clusters, interactions, and run history in SQLite (`pulsebrief.db`).
8. **Deliver** — Send per-topic pushes (ntfy includes priority + an Open Article button) or print the full Morning Brief.

## Digest Format (Morning Brief)

```
Morning Brief — Saturday, June 13, 2026

TOP STORIES
1. [Tech] phpBB forum fixes auth bypass bug lurking for a decade (importance 9)
   A 10-year-old authentication bypass vulnerability was discovered...

CYBERSECURITY
- China-Linked Hackers Backdoored Linux Login Software... [Opinion/Analysis]
  Source: The Hacker News
  TLDR: …
  Why it matters: …
  Link: …

WATCHLIST
- Likely to develop: …
- Underreported: …
- Conflicting reports: …
```

Per-topic phone notifications use the topic as the title and a numbered, source-labeled body; reply (Twilio/Slack) or run locally with `more <n>`, `full <n>`, `explain <n>`.

## Project Structure

```
pulsebrief/
  app/
    main.py              # FastAPI app
    config.py            # Settings and topics loader
    database.py          # SQLAlchemy setup
    models.py            # ORM models
    schemas.py           # Pydantic schemas
    services/
      news_fetcher.py    # NewsAPI + GDELT, canonical URLs, dedup, opinion detection
      summarizer.py      # Triage + rich Groq/OpenAI summaries + explain()
      ranker.py          # Ranking with caps, reputation, memory/preference signals
      clustering.py      # Story clustering (no extra LLM calls)
      brief.py           # Morning Brief formatter
      preferences.py     # Mutes / topic edits (preferences.yaml, topics.yaml)
      memory.py          # save/ignore interactions + summary-failure queue (SQLite)
      sender.py          # Delivery channel factory
      ntfy_sender.py     # ntfy push delivery (priority + action button)
      twilio_sender.py   # Twilio SMS/WhatsApp delivery + formatting
      slack_sender.py    # Slack delivery
      digest_service.py  # Orchestration
    jobs/
      scheduler.py       # Recurring APScheduler job
  cli.py
  topics.yaml
  preferences.yaml       # your mutes/preferences (committed, used by cloud runs too)
  requirements.txt
  .env.example
  README.md
```

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| `429 Too Many Requests` from Groq | Free-tier per-minute limit. Use `GROQ_MODEL=llama-3.1-8b-instant`, lower `CANDIDATES_PER_TOPIC`, or wait a minute — the client retries automatically. |
| Summaries look generic / lack insight | No working AI key — set `GROQ_API_KEY`. Without it, PulseBrief uses an extractive fallback (no importance, category, or bias). |
| `database is locked` | Two processes hit SQLite at once. Avoid running the CLI while the API server is also running a digest; the engine waits up to 30s. |
| Few or no stories delivered | `MIN_IMPORTANCE` may be too strict, or mutes too broad. Lower `MIN_IMPORTANCE` or check `preferences.yaml`. |
| Cloud digest ignores my mutes | Mutes live in `preferences.yaml` — commit and push it so the GitHub Actions runner picks it up. |
| NewsAPI returns nothing | Missing/invalid `NEWS_API_KEY` (falls back to GDELT) or rate-limited; check logs. |

On startup and on `python cli.py run`, PulseBrief logs any configuration problems (missing keys, incomplete delivery setup) so you know what's degraded.

## Future Improvements

- Email digest delivery
- Web UI for topic management and article browsing
- Per-topic delivery schedules
- RSS feed support
- LLM-assisted clustering and explicit conflict detection across sources
- Multi-user support with separate topic profiles
- Docker Compose packaging
- Slack interactive buttons / ntfy Save & Ignore actions via a hosted endpoint

## License

MIT

# PulseBrief

A local-first personal AI news digest app. PulseBrief fetches daily world news for topics you choose, summarizes them into TLDR format, stores everything in SQLite, and delivers a digest via Twilio (SMS/WhatsApp) or Slack, with a CLI/console fallback.

## Features

- **Topic configuration** via `topics.yaml`
- **News sourcing** from NewsAPI (primary) or GDELT (fallback)
- **Smart ranking** by recency, relevance, source diversity, and reputation
- **AI summarization** via Groq (free) or OpenAI, with extractive fallback
- **Pluggable delivery** — ntfy (free push), Twilio (SMS/WhatsApp), Slack, or console, chosen via `DELIVERY_CHANNEL`
- **Per-topic notifications** — one push per topic (title = topic, body expands to headlines)
- **Two-way commands** (Twilio/Slack): `more 1`, `full 1`, `topics`, `run digest`
- **CLI** for local use without any messaging account
- **REST API** via FastAPI
- **Recurring scheduling** via APScheduler (every N hours)

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
   | `GROQ_MODEL` | Groq model (default `llama-3.3-70b-versatile`) |
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
   | `MAX_ARTICLES_PER_TOPIC` | Max articles per topic (default: 4) |
   | `MAX_TOTAL_ARTICLES` | Max total articles across all topics (default: 40) |

5. **Customize topics** in `topics.yaml` (see below).

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
python cli.py run-digest
```

Skip delivery and print locally only:

```bash
python cli.py run-digest --no-send
```

**Other CLI commands:**

```bash
python cli.py topics
python cli.py more 1
python cli.py full 1
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/topics` | List configured topics |
| POST | `/digest/run` | Run digest now |
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

1. **Fetch** — For each topic, query NewsAPI or GDELT for recent English articles.
2. **Deduplicate** — Remove duplicates by URL and similar titles.
3. **Rank** — Score by recency, keyword relevance, source reputation, and diversity.
4. **Summarize** — Generate TLDR, "why it matters", and long summary via OpenAI.
5. **Store** — Persist articles and digest runs in SQLite (`pulsebrief.db`).
6. **Deliver** — Send the formatted digest via the configured channel (Twilio SMS/WhatsApp, Slack, or console).

## Digest Format

```
Good morning — here's your PulseBrief.

1. [AI] Article title
   Source: Reuters
   TLDR: …
   Why it matters: …
   Link: …

Reply with: more 1 | more 2 | full 1 | topics
```

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
      news_fetcher.py    # NewsAPI + GDELT
      summarizer.py      # OpenAI summarization
      ranker.py          # Article ranking
      sender.py          # Delivery channel factory
      ntfy_sender.py     # ntfy push delivery (free)
      twilio_sender.py   # Twilio SMS/WhatsApp delivery
      slack_sender.py    # Slack delivery
      digest_service.py  # Orchestration
    jobs/
      scheduler.py       # Daily APScheduler job
  cli.py
  topics.yaml
  requirements.txt
  .env.example
  README.md
```

## Future Improvements

- Email digest delivery
- Web UI for topic management and article browsing
- Per-topic delivery schedules
- RSS feed support
- User feedback loop (thumbs up/down) to improve ranking
- Multi-user support with separate topic profiles
- Docker Compose packaging
- Slack interactive buttons instead of text replies
- Article clustering for better duplicate-story detection
- Caching and rate-limit handling for API quotas

## License

MIT

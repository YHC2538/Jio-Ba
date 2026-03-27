# Jio-Ba

AI-assisted Discord bot for organizing group activities through structured DM interviews, host review, and final plan adjudication.

[Traditional Chinese README](./README.zh-TW.md)

## What This Project Does

Jio-Ba helps a host run an activity flow end to end:

1. Create an activity card in a server using `/jio`.
2. Let participants join with a button.
3. Interview participants in DM with AI-guided, question-by-question collection.
4. Handle off-topic or risky replies with warning and ON_HOLD review flow.
5. Generate candidate plans for the host and finalize one plan.
6. Announce the final result and optionally create a Discord Scheduled Event.

## Key Features

- Server-only activity creation with `/jio`.
- Rich Discord UI with modals, buttons, and select menus.
- Loading animation while creating the activity card.
- Configurable signup and interview deadlines.
- Dynamic interview question generation from title/description/seeds.
- Per-user concurrent DM interview processing:
   same activity can interview multiple participants at once.
- Debounced processing per participant to avoid noisy over-replies.
- Multi-turn answer accumulation:
   partial answers can be merged across turns before final acceptance.
- Warning policy with threshold and ON_HOLD escalation.
- Host adjudication UI for ON_HOLD participants: CONTINUE or KICK.
- Confirm/Edit final submission flow for participant answers.
- Dashboard updates in channel with participant states.
- Export command for event status snapshots.

## Current Commands

- `/jio`
   create a new activity (supports optional fields such as title and limits).
- `/verdict`
   open ON_HOLD adjudication UI for host.
- `/export_status`
   export event status as JSON.

## High-Level Flow

1. Host runs `/jio` in a server channel.
2. Bot shows creation modal, then posts activity embed with `Join` button.
3. Participants join and are moved into interview pipeline.
4. Bot sends DM interview overview and asks questions one by one.
5. Participant state moves through `PENDING`, `INTERVIEWING`, `READY`, `ON_HOLD`, or `KICKED`.
6. Host can review ON_HOLD participants and decide continue/kick.
7. Bot prepares candidate plans and host selects final plan.

## Architecture

- `main.py`
   bootstraps the bot, validates environment, loads cogs.
- `cogs/jio.py`
   slash commands, Discord UI, state dashboard, interview orchestration.
- `cogs/ai_brain.py`
   queueing, debouncing, and LangGraph invocation orchestration.
- `cogs/graph_agent.py`
   interview graph nodes: gatekeeper, extractor, reprompt, finalize, hold, malicious.
- `cogs/db.py`
   MongoDB CRUD, participant/event state transitions, warning policy helpers.
- `cogs/matching/nsw_calculator.py`
   candidate ranking helpers.

## Data and State Highlights

- Event document stores:
   activity metadata, interview question set, participants, warning policy, workflow status.
- Participant interview stores:
   `current_question_id`, `answers`, `draft_answers`, `revision_count`, `confirmed`, `completed`.
- Warning policy stores:
   threshold and final revision limits.

## Requirements

- Python `>=3.10,<3.11`
- MongoDB
- Discord bot token
- Google API key for Gemini access

Core dependencies are listed in `requirements.txt` and `pyproject.toml`.

## Environment Variables

Required in `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
MONGO_URI=your_mongodb_uri
GOOGLE_API_KEY=your_google_api_key
GOOGLE_API_ENDPOINT=https://generativelanguage.googleapis.com
```

Optional:

```env
GEMINI_MODEL_NAME=gemini-2.0-flash
```

## Local Setup

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

If your machine has multiple Python installations, run with the venv interpreter directly.

## Production

`ecosystem.config.js` is included for PM2-based process management.

```bash
pm2 start ecosystem.config.js
pm2 logs
pm2 save
```

## Operational Notes

- Message Content Intent is required for DM interview behavior.
- If privileged intents are missing, startup may switch to limited mode.
- `/jio` must be used in a server, not DM.
- This project currently enforces a single active interview context per user to reduce cross-event confusion.

## Testing and Diagnostics

Utility scripts in repository include:

- `test_llm_connection.py`
- `test_google_search.py`
- `verify_agent.py`
- `show_cost.py`
- `inspect_genai.py`

## License

See project license file if provided.

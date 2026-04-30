# ohrmin-claw — Personal AI Health Assistant

[한국어](./README.md) | **English**

> Name: a mashup of "Oh Yeseong" + Claude's "Claw"

An AI health assistant that unifies Garmin watch, Apple Health, and body-composition data so you can manage your health through natural-language chat on Discord.
Claude AI applies sports physiology, sleep medicine, and body-composition analysis frameworks to deliver science-backed insights.

---

## What makes it different?

Generic health dashboards show you numbers. ohrmin-claw **interprets** them.

- **Ask in plain language** — Chat like "How was my sleep this week?" or "Analyze my recent workouts" and the AI responds
- **Conversations flow naturally** — Follow-up questions work inside Discord threads; previous context is remembered
- **Proactively notifies you** — When new body-composition data arrives, it analyzes and sends an alert before you even ask
- **Science-backed** — Analysis grounded in ACSM, NSCA, Daniels VDOT, Israetel MEV/MAV/MRV, and other exercise-physiology literature
- **Long-term memory** — Automatically learns your health patterns and preferences, becoming more personalized over time

---

## Key Features

| Feature | Example |
|---------|---------|
| Sleep analysis | "How was my sleep this week?" |
| Workout evaluation | "Analyze yesterday's run" |
| Body-comp trends | "How has my body fat changed this month?" |
| Recovery status | "Am I recovering well after workouts?" |
| Body-comp entry | "InBody result: weight 72 kg, body fat 15.2%, muscle mass 34.5 kg" |
| Image analysis | Attach InBody printout or meal photo |
| Weekly report | "Weekly report" |
| Training plan | "Plan next week's workouts" |

### Sport-specific workout analysis

Claude auto-detects the activity type from Garmin data and delivers detailed analysis.

- **Running** — Lap-by-lap pace/cadence/VO2max, HR zone distribution, Daniels VDOT mapping
- **Weight training** — Exercise name/load/reps, volume by muscle group, progressive overload assessment
- **Swimming** — SWOLF/stroke count, CSS-based intensity diagnosis
- **Cycling / Hiking** — FTP-based power zones, TSS/IF, grade-adjusted pace

### Automation

- **Apple Health auto-sync** — Polls iCloud every 2 minutes, detects new body-composition data, and automatically sends a Claude analysis to Discord
- **Weekly report** — 7-day summary of sleep, heart rate, HRV, activity, stress, and body composition with AI insights

---

## Screenshots

<!-- TODO: Add Discord bot GIF/screenshots -->
_Coming soon: thread-based conversations, automated insights._

---

## Data Sources

| Source | Integration | Data collected |
|--------|------------|----------------|
| **Garmin Connect** | Direct API via python-garminconnect | Sleep, heart rate, HRV, stress, detailed activity data |
| **Apple Health** | Health Auto Export app → iCloud → 2-min polling | Weight, body fat %, lean mass, BMI |
| **Manual entry** | Natural-language Discord chat | Body-composition figures (InBody, scale, etc.) |
| **Images** | Discord attachments (up to 5, 10 MB) | InBody printouts, meal photos |

---

## Prerequisites

| Item | Notes |
|------|-------|
| **Python 3.11+** | Verify with `python3 --version` |
| **Claude Code subscription** | Run `claude login` in your terminal |
| **Garmin Connect account** | [connect.garmin.com](https://connect.garmin.com) |
| **Discord bot token** | Create one at the [Developer Portal](https://discord.com/developers/applications) |

### Discord bot setup

1. Developer Portal → create app → Bot → Reset Token
2. **Bot** tab → enable **Message Content Intent** (required)
3. **OAuth2** → URL Generator → Scopes: `bot`
4. Bot Permissions: `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Read Message History`
5. Invite the bot to your server using the generated URL

---

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd ohrmin-claw
```

### 2. Initial setup

```bash
bash scripts/setup.sh
```

Installs dependencies, creates required directories, and copies `.env.example` → `.env`.
If `.env` does not exist it is created automatically and the script exits — fill in the values and run again.

### 3. Configure environment variables

Open `.env` and fill in the following.

```env
# Required
GARMIN_USERNAME=your-garmin-email@example.com
GARMIN_PASSWORD=your-garmin-password
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_APPLICATION_ID=your-application-id
ALLOWED_USERS=123456789012345678    # Discord User ID (comma-separated)

# Optional
LLM_ADAPTER=claude                  # default
LLM_MODEL=claude-sonnet-4-20250514  # default
MEMORY_MODE=auto                    # auto | manual
SESSION_IDLE_TIMEOUT=1440           # minutes (default: 24 hours)
NOTIFY_CHANNEL_ID=                  # channel for auto-analysis alerts (disabled if unset)
APPLE_HEALTH_EXPORT_DIR=            # Health Auto Export iCloud path (has a default)
```

> Leaving `ALLOWED_USERS` empty causes the bot to ignore all messages (whitelist mode).
> Enable Developer Mode in Discord settings → right-click your profile → "Copy User ID".

### 4. Start the bot

```bash
python3 bot/main.py
```

```
✅ Garmin Connect login successful
✅ Garmin MCP tools registered
🔒 Allowed users: 1
🚀 ohrmin-claw bot started...
📊 Apple Health auto-sync started (2-min interval)
```

Even if Garmin login fails the bot starts normally (body-composition features still work).

---

## Usage

Send a message in any Discord channel where the bot is present — a **thread is created automatically**.
Ask follow-up questions inside the thread; the bot references the entire conversation history.

> Note: The example conversations below are in Korean because that is this project's primary use case. The bot understands and responds in any language.

```
이번 주 수면 어때?
→ (thread created) AI analyzes 7 days of sleep data and replies

어제보다 나아졌어?
→ (same thread) AI references the previous answer for a comparative analysis
```

### Body-composition entry

```
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1
```

Past dates are supported:

```
인바디 결과 2026-03-15 체중 73kg 체지방률 16.1%
```

### Apple Health auto-sync

Install **Health Auto Export** on your iPhone and configure automatic export to iCloud Drive. The bot polls every 2 minutes, detects new entries, and sends an analysis. `NOTIFY_CHANNEL_ID` must be set to receive alerts.

---

## Automation (cron)

```cron
# Daily at 06:00 — validate/refresh Garmin token
0 6 * * * /path/to/ohrmin-claw/garmindb/sync.sh

# Every Sunday at 03:00 — data backup
0 3 * * 0 /path/to/ohrmin-claw/scripts/backup.sh
```

Apple Health sync and auto-analysis run inside the bot process on a 2-minute loop.

---

## Customizing your goals

Edit `prompts/goals.md` — changes take effect on the next query without restarting the bot.

```markdown
# prompts/goals.md example

## Current goals
- Diet toward target weight of 92 kg
- Maintain maximum muscle mass
- Improve running performance
- Average sleep ≥ 7 hours
```

---

## Architecture

```
User (Discord chat)
       │
       ▼
 Discord bot receives message → thread created automatically
       │
       ├─ Base context: last 7-day Garmin summary (always included)
       │
       ▼
 Claude AI (system prompt + health context + conversation history)
       │
       ├─ When deep analysis needed → MCP tool calls (Garmin / body-comp / memory)
       │
       ▼
 Reply sent to thread (streamed TextBlock by TextBlock)
```

### Core patterns

- **Adapter pattern** — Both LLM and messaging channel are abstracted behind ABCs; swap implementations by changing `.env`
- **In-process MCP server** — `claude_agent_sdk`'s `@tool` + `create_sdk_mcp_server()` serve tools inside the bot process (no separate process needed)
- **Hybrid data access** — Summary context is always included; detailed data fetched on demand via MCP tools (saves tokens)
- **Context compression** — When conversation exceeds 20 turns, the middle segment is summarized by the LLM (first 1 + last 6 messages protected)
- **Persistent memory** — Health patterns and user preferences are automatically extracted from conversations and stored long-term
- **Specialist skills** — Exercise evaluation, sleep analysis, body-composition, and science-reference frameworks loaded from `.claude/skills/`

---

## Directory Structure

```
ohrmin-claw/
├── bot/
│   └── main.py                 # bot entry point
├── core/
│   ├── llm.py                  # LLM adapter (ClaudeSDKAdapter)
│   ├── channel.py              # channel adapter (DiscordChannel)
│   ├── garmin_data.py          # Garmin Connect API client
│   ├── garmin_tools.py         # Garmin MCP tools (9)
│   ├── body_metrics.py         # body-comp CSV CRUD
│   ├── body_metrics_tools.py   # body-comp MCP tools (3)
│   ├── body_metrics_parser.py  # natural-language body-comp parser
│   ├── memory.py               # persistent memory management
│   ├── memory_tools.py         # memory MCP tools (4)
│   ├── preprocessor.py         # raw data → statistical summary
│   ├── report.py               # weekly markdown report
│   ├── context_compressor.py   # conversation history compression
│   ├── session_manager.py      # thread session timeout
│   └── apple_health_reader.py  # iCloud → inbody.csv sync
├── prompts/
│   ├── system.md               # AI persona
│   ├── goals.md                # personal health goals
│   ├── memory.md               # auto-extracted long-term memory
│   └── user.md                 # user preferences
├── .claude/skills/             # specialist analysis skills
│   ├── activity-evaluation/    # workout evaluation
│   ├── body-composition/       # body-comp analysis
│   ├── sleep-analysis/         # sleep analysis
│   └── science-reference/      # science reference values
├── data/
│   └── inbody.csv              # body-composition data
├── tests/                      # tests (20 files)
├── scripts/
│   ├── setup.sh                # initial environment setup
│   └── backup.sh               # data backup
└── garmindb/
    └── sync.sh                 # Garmin token validation/refresh
```

---

## Tech Stack

| Item | Details |
|------|---------|
| Language | Python 3.11+ |
| AI | [Claude Agent SDK](https://github.com/anthropics/claude-code/tree/main/packages/agent-sdk) (subscription model) |
| Discord | [discord.py](https://github.com/Rapptz/discord.py) |
| Garmin | [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) (direct API calls) |
| Data | CSV (body composition) |
| Runtime | macOS native (no Docker) |

---

## Tests

```bash
python3 -m pytest tests/ -v
```

Every core module has a corresponding test file. Garmin tests use mocks — no real API calls required.

---

## Troubleshooting

### Bot not responding

1. Check that your Discord User ID is listed in `ALLOWED_USERS`
2. Verify `DISCORD_BOT_TOKEN` is correct
3. Confirm **Message Content Intent** is enabled in the Discord Developer Portal

### Garmin authentication error

```bash
bash garmindb/sync.sh
```

Manually validate/refresh the token. Garmin may return 429 for frequent login attempts.

### Body-composition parsing fails

Make sure the message includes numbers with units:

```
# Correct
인바디 결과 체중 72kg 체지방률 15.2% 골격근량 34.5kg BMI 22.1
```

---

## License

MIT License — see [LICENSE](./LICENSE)

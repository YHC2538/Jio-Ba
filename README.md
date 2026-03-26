# Jio-Ba Discord Bot

A smart Discord bot that helps groups coordinate dining plans with interview-driven preference matching, NSW recommendations, and host adjudication.

[中文版 (Chinese Version)](./README.zh-TW.md)

## Features

- **Event Creation**: Easily organize dining events with `/jio`.
- **Decision Modes**:
   - 🤝 **Consensus**: Auto-adopts top recommendation after report generation.
  - 🎲 **Random**: Randomly picks a winner.
   - 👑 **Dictator**: The organizer picks final plan from top recommendations.
- **Interactive UI**: Discord Buttons and Modals for joining and setting preferences.
- **DM Interview Flow**: Participants answer in DM, and can select active event when joining multiple events.
- **Scheduled Event Output**: Final adjudicated plan is announced and converted to Discord Scheduled Event.

## Prerequisites

- Python 3.10 or higher
- MongoDB Database
- Discord Bot Token
- Google Gemini API Key

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository_url>
   cd <repository_name>
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. **Environment Variables**
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`**
   Fill in your API keys and configuration:
   ```ini
   DISCORD_TOKEN=your_token
   MONGO_URI=your_mongo_uri
   GOOGLE_API_KEY=your_google_key
   GOOGLE_API_ENDPOINT=https://generativelanguage.googleapis.com
   ```

## Running the Bot

### Local Development
```bash
python main.py
```

### Production Deployment (PM2)
This project includes an `ecosystem.config.js` for PM2.

```bash
# Start the bot
pm2 start ecosystem.config.js

# Monitor logs
pm2 logs discord-bot

# Save configuration to restart on boot
pm2 save
pm2 startup
```

## Usage

### Commands

- **`/jio`**: Start a new dining event.
  - Optional arguments: `title`, `time_limit`, `decision_mode`.
- **`/export_status`**: Export the current event status as a JSON file.

### How it works

1. User runs `/jio` to create an event.
2. Bot posts an embed with a "Join" button.
3. Participants click "Join" and fill in their preferences (e.g., "Ramen", "Not spicy").
4. **AI Mode**:
   - The bot DMs participants to collect structured preferences and dealbreakers.
   - If users are in multiple events, they choose active event in DM by index.
5. **Interview Phase**:
   - When deadline is reached or all interviews are complete, NSW generates top candidates.
6. **Adjudication & Event**:
   - Dictator mode: host clicks plan button.
   - Consensus mode: system auto-adopts plan 1.
   - Bot posts final announcement and creates a Discord Scheduled Event.

## License

[MIT](LICENSE)

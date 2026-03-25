# Wei-Jia-Ba (未呷飽) Discord Bot

A smart Discord bot that helps groups decide where to eat using AI, random selection, or dictator mode.

[中文版 (Chinese Version)](./README.zh-TW.md)

## Features

- **Event Creation**: Easily organize dining events with `/jio`.
- **Decision Modes**:
  - 🤖 **AI**: Uses LLM to analyze preferences and suggest a restaurant.
  - 🎲 **Random**: Randomly picks a winner.
  - 👑 **Dictator**: The organizer decides.
- **Interactive UI**: Discord Buttons and Modals for joining and setting preferences.
- **Smart Coordination**: AI acts as a facilitator, asking follow-up questions to clarify vague preferences.

## Prerequisites

- Python 3.10 or higher
- MongoDB Database
- Discord Bot Token
- OpenRouter API Key
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
   OPENROUTER_API_KEY=your_openrouter_key
   OPENROUTER_API_ENDPOINT=https://openrouter.ai/api/v1
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
   - The bot may DM participants to clarify preferences.
   - Once everyone is ready or time is up, the AI suggests a restaurant.
5. **Interview Phase**:
   - When the event is closed (manually or via time limit), the bot enters the interview phase to finalize the decision.

## License

[MIT](LICENSE)

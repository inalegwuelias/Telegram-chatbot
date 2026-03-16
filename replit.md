# AI Telegram Chatbot

## Overview
An AI-powered Telegram bot with a Flask web dashboard for management. The bot provides group moderation, AI responses via OpenAI Assistants API, and violation tracking.

## Project Structure
```
AI telegram chatbot/AI bot/
├── main.py           # Flask web dashboard (port 5000)
├── telegram_bot.py   # Telegram bot logic
├── templates/
│   └── index.html    # Dashboard UI
├── violations.json   # Persistent violation tracking
├── .env.example      # Required environment variables template
└── requirements.txt  # Python dependencies
```

## Setup
- **Language:** Python 3.12
- **Framework:** Flask 3.0 (web dashboard), python-telegram-bot 20.7
- **AI:** OpenAI Assistants API

## Required Environment Variables
- `TELEGRAM_TOKEN` — Telegram bot token from BotFather
- `OPENAI_API_KEY` — OpenAI API key
- `ASSISTANT_ID` — Pre-configured OpenAI Assistant ID
- `SESSION_SECRET` — Flask session secret key

## Running
The workflow starts the Flask dashboard:
```
cd "AI telegram chatbot/AI bot" && python3 main.py
```
The dashboard runs on `0.0.0.0:5000`. From the dashboard, you can start/stop the Telegram bot subprocess.

## Deployment
Configured as a VM deployment (always-running) using gunicorn:
```
cd "AI telegram chatbot/AI bot" && gunicorn --bind=0.0.0.0:5000 --reuse-port main:app
```

## Features
- Web dashboard to start/stop bot and monitor logs
- AI responses using OpenAI Assistants API
- Group moderation: detects hate speech, spam, and external links
- Violation tracking with auto-ban (3 violations = 24h ban, 5 = permanent)
- Admin commands: /ban, /tempban, /unban, /warn, /unwarn, /reset, /stats

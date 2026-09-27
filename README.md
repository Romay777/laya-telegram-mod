# Laya Moderator

[Русская версия](README.ru.md)

A self-hosted Telegram bot that checks every message in your group with an AI classifier. It deletes spam, advertising and insults, and restricts the sender on an escalating penalty ladder.

> **Status:** design phase. The architecture is documented, and there is no working code yet.

## Features

- **AI moderation.** Every text message and caption is checked, including edits. By default the bot uses [Laya](https://huggingface.co/convaiinnovations/laya), which runs locally on CPU. You can switch to the hosted [Jev](https://docs.typesafe.ai) API instead.
- **Two confidence zones.** High-confidence hits are acted on automatically. Borderline messages are sent to admins, who choose *Punish* or *Dismiss*.
- **Observation mode first.** A new chat starts in dry-run: the bot shows you what it would do and leaves everything as it is. You switch on auto-moderation when you trust it.
- **Configurable penalty ladder.** The default is 1 hour → 24 hours → forever. Each violation expires after 30 days. Members are restricted from writing, never kicked.
- **Appeals.** A restricted member presses "It's a mistake" and admins get the deleted message quoted, with *Lift restriction* / *Reject* buttons.
- **Custom notices.** You can write your own restriction notice with full formatting and `{user}`, `{reason}`, `{duration}` and `{strike}` placeholders.
- **One-message admin UI.** The bot is managed through a single inline menu that edits itself as you navigate, with coloured buttons (Bot API 9.4+).
- **English and Russian.** The admin interface language and each chat's language are set separately.

## Quick start

Requirements: Docker with Compose, and roughly 3 GB of RAM if you run the local Laya model.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Configure and start:

   ```bash
   git clone https://github.com/Romay777/laya-telegram-mod.git
   cd laya-telegram-mod
   cp .env.example .env    # set BOT_TOKEN and POSTGRES_PASSWORD
   docker compose up -d --build
   ```

3. Open your bot in Telegram, press **Start**, pick a language, and tap **Add to chat**. Telegram lets you pick a supergroup and grants the bot the rights it needs: delete messages and restrict members.

### Using Jev instead of (or alongside) Laya

Set `JEV_API_KEY` in `.env`. `JEV_BASE_URL` defaults to TypeSafe. Set it to `https://openrouter.ai/api/v1` to use OpenRouter instead. You can then choose the backend per chat in the bot's settings. To run Jev only and skip the local model, remove `COMPOSE_PROFILES=laya` from `.env`.

With Jev enabled, message texts from that chat are sent to a third-party API.

## Documentation

- [Architecture](docs/ARCHITECTURE.md): components, moderation pipeline, data model, menu map
- [Domain glossary](CONTEXT.md)
- [Architecture decisions](docs/adr/)
- [Roadmap](docs/ROADMAP.md)

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

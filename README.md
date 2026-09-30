# Laya Moderator

[Русская версия](README.ru.md)
[![CI](https://github.com/Romay777/laya-telegram-mod/actions/workflows/ci.yml/badge.svg)](https://github.com/Romay777/laya-telegram-mod/actions/workflows/ci.yml)

A self-hosted Telegram bot that checks every message in your group with an AI classifier. It deletes spam, advertising and insults, and restricts the sender on an escalating penalty ladder.

> **Status:** the v1 feature set is implemented and covered by tests. Run it from source with Docker — there are no packaged releases yet. What is deliberately deferred is listed in the [roadmap](docs/ROADMAP.md).

## Features

- **AI moderation.** Every text message and caption is checked, including edits. By default the bot uses [Laya](https://huggingface.co/convaiinnovations/laya), a local model served on CPU as INT8 ONNX. You can switch to the hosted [Jev](https://docs.typesafe.ai) API per chat; if Jev is overloaded or unavailable, the bot falls back to Laya and tells admins when the backend recovers.
- **Two confidence zones.** High-confidence hits are acted on automatically. Borderline messages go to admins as suspicion alerts with *Punish* / *Dismiss* buttons — the first admin to click wins. Unanswered alerts close themselves after 24 hours.
- **Observation mode first.** A new chat starts in dry-run: every flag becomes a suspicion alert, and nothing is deleted or restricted. A summary of what the bot would have done arrives every 48 hours. You switch on auto-moderation when you trust it.
- **Configurable penalty ladder.** The default is 1 hour → 24 hours → forever. Each violation expires after 30 days. Members are restricted from writing, never kicked.
- **Appeals.** A restricted member presses "It's a mistake" and admins get the deleted message quoted, with *Lift restriction* / *Reject* buttons.
- **Custom notices.** You can write your own restriction notice with full formatting and `{user}`, `{reason}`, `{duration}` and `{strike}` placeholders. Notices are rate-limited per chat.
- **One-message admin UI.** The bot is managed through a single inline menu that edits itself as you navigate, with coloured buttons (Bot API 9.4+). It includes a statistics screen (7-day and 30-day windows) and a paginated journal of violations with a card for each one.
- **Self-monitoring lifecycle.** If the bot loses its rights in a chat, it suspends moderation there and alerts the admins; adding it back restores the chat's settings.
- **English and Russian.** The admin interface language and each chat's language are set separately.

## Quick start

Requirements: Docker with Compose. The local model container is capped at 3 GB of RAM (the INT8 model itself takes 1–2 GB); Postgres and the bot add a few hundred megabytes.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Configure and start:

   ```bash
   git clone https://github.com/Romay777/laya-telegram-mod.git
   cd laya-telegram-mod
   cp .env.example .env    # set BOT_TOKEN and POSTGRES_PASSWORD
   docker compose up -d --build
   ```

   The first build downloads the pinned Laya checkpoint and exports it to INT8 ONNX, so it takes a while; after that everything is baked into the image and the bot never contacts Hugging Face at runtime.

3. Open your bot in Telegram, press **Start**, pick a language, and tap **Add to chat**. Telegram lets you pick a supergroup and grants the bot the rights it needs: delete messages and restrict members.

### Using Jev instead of (or alongside) Laya

Set `JEV_API_KEY` in `.env`. `JEV_BASE_URL` defaults to TypeSafe. Set it to `https://openrouter.ai/api/v1` to use OpenRouter instead; `JEV_MODEL` selects the model. You can then choose the backend per chat in the bot's settings. To run Jev only and skip the local model, remove `COMPOSE_PROFILES=laya` from `.env`.

With Jev enabled, message texts from that chat are sent to a third-party API.

## Configuration

`.env` holds only what you must set: the bot token, the Postgres password, the Jev connection and the log level. Everything else — confidence thresholds per backend and sensitivity level, notice rate limits, retention periods — lives in [`src/bot/config.toml`](src/bot/config.toml) with defaults for every value. To override it, mount your own copy at the same path in the `bot` container.

## Development

Both subprojects are Python 3.12 managed with [uv](https://docs.astral.sh/uv/):

```bash
cd src/bot
uv sync
uv run pytest            # integration and e2e tests run a real Postgres 17 via testcontainers (needs Docker)
uv run ruff check .

cd ../laya-server
uv run pytest -q
```

CI runs the same checks for both projects on every push and pull request. The test strategy is described in the [architecture document](docs/ARCHITECTURE.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md): components, moderation pipeline, data model, menu map
- [Domain glossary](CONTEXT.md)
- [Architecture decisions](docs/adr/)
- [Roadmap](docs/ROADMAP.md)

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

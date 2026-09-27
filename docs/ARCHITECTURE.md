# Architecture

Status: design for v1. No code is written yet. Terms in **bold** come from [CONTEXT.md](../CONTEXT.md). The decisions behind this design are in [docs/adr/](adr/), and deferred work is in [ROADMAP.md](ROADMAP.md).

## 1. Overview

```
              Telegram Bot API (long polling)
                         │
                 ┌───────▼────────┐          HTTPS, optional
                 │      bot       │─────────────────────────► Jev (TypeSafe / OpenRouter)
                 │  aiogram 3.31  │
                 │                │   HTTP POST /v1/systemone
                 │                │───────────────┐
                 └───────┬────────┘               │
                         │ asyncpg         ┌──────▼────────┐
                 ┌───────▼────────┐        │     laya      │
                 │    postgres    │        │  laya-serve + │
                 └────────────────┘        │   INT8 ONNX   │
                                           └───────────────┘
            internal Docker network only, no published ports
```

| Service | Source | Role |
|---|---|---|
| `bot` | `src/bot` | aiogram app containing the moderation pipeline, **Menu**, **Admin Alerts** and scheduler. Runs Alembic migrations on start. |
| `postgres` | `postgres:17` | Holds all persistent state, including aiogram FSM state. |
| `laya` | `src/laya-server` | Serves the Laya `multilingual/` checkpoint as INT8 ONNX behind `laya-serve`'s `/v1/systemone`. Compose profile `laya`, enabled by default. |

The bot runs as one process. Anything that has to happen later is stored as a due timestamp in Postgres, so it survives restarts: removing a **Chat Notice**, closing a **Suspicion**, sending the Observation summary, purging stored text. An in-process scheduler loop picks these up (§11). Telegram lifts expired **Restrictions** itself, so no job is needed for that.

## 2. Repository layout

```
src/
  bot/
    pyproject.toml            # uv project, no torch
    Dockerfile
    config.toml               # tunables with defaults (§3)
    migrations/               # Alembic
    app/
      main.py                 # wiring: settings, DB, Bot, Dispatcher, scheduler
      config.py               # .env + config.toml → typed settings
      clock.py                # Clock protocol: real and fake
      domain/                 # pure logic: no Telegram, no DB, no I/O
        ladder.py             # Penalty Ladder, Step selection, Expiry
        decision.py           # thresholds + signals + mode → Decision
        template.py           # Notice Template validation and rendering
      classifiers/
        spec.py               # versioned System One question spec for Categories
        client.py             # SystemOneClient, used for both Laya and Jev
        router.py             # per-chat backend, fallback, concurrency, health, incidents
      moderation/
        pipeline.py           # message → filters → signals → Verdict → Decision → actions
        signals.py
        actions.py            # delete, restrict, lift, ban sender chat
      notices/
        sender.py             # per-chat rate-limited Chat Notice queue
      alerts/                 # Admin Alert fan-out, first-click-wins, cross-editing
      linking/                # Linking flow, rights checks, admin-status cache
      menu/
        navigator.py          # renders a screen into the single Menu message
        callbacks.py          # CallbackData factories
        screens/              # one module per screen
      telegram/
        handlers/             # group message/edit, my_chat_member, chat_member, private
        middlewares.py        # DB session, i18n locale, admin cache
      scheduler.py
      db/
        models.py
        repositories/
        fsm_storage.py        # aiogram BaseStorage backed by Postgres
      i18n/locales/{en,ru}/   # menu.ftl, alerts.ftl, notices.ftl
    tests/{unit,integration,e2e}/
  laya-server/
    Dockerfile                # multi-stage: export INT8 ONNX, then runtime
    app.py                    # ONNXAgent → Router.attach → laya.serve.create_app
docker-compose.yml
.env.example
.github/workflows/ci.yml
```

`src/bot` and `src/laya-server` are separate Python projects with their own dependencies. The bot never imports torch, and the Laya server never imports aiogram.

Stack: Python 3.12, uv, aiogram 3.31, SQLAlchemy 2 (async) with asyncpg, Alembic, aiogram-i18n with Fluent, httpx, ruff, and pytest with testcontainers.

## 3. Configuration

### `.env` (Operator-facing, kept short)

```dotenv
BOT_TOKEN=
POSTGRES_PASSWORD=

# Remove this line to run without the local Laya model (Jev only)
COMPOSE_PROFILES=laya

# Optional: Jev via TypeSafe (default) or OpenRouter (https://openrouter.ai/api/v1)
JEV_API_KEY=
JEV_BASE_URL=https://api.typesafe.ai/v1
JEV_MODEL=jev-1.13.0

LOG_LEVEL=INFO
```

- Compose builds `DATABASE_URL` and the internal Laya URL. Neither is an Operator setting.
- Jev is available only when `JEV_API_KEY` is set.
- The client appends `/systemone` to `JEV_BASE_URL`.

### `config.toml` (shipped in the image, override by mounting)

Operators normally never touch this file. Every value has a default.

| Section | Keys (default) |
|---|---|
| `[thresholds.<backend>.<sensitivity>]` | `violation`, `suspicion`. Starting values below, not calibrated. |
| `[signals]` | threshold adjustments, see below |
| `[classifier]` | `timeout_s = 3`, `max_concurrency = 4`, `health_interval_s = 60` |
| `[moderation]` | `min_words = 3`, `new_member_hours = 24`, `new_member_messages = 3` |
| `[notices]` | `per_second = 1`, `per_minute = 18`, `max_queue_age_s = 300`, `max_lifetime_h = 24`, `outcome_visible_s = 600` |
| `[suspicions]` | `auto_close_h = 24` |
| `[observation]` | `summary_after_h = 48` |
| `[retention]` | `flagged_text_days = 30`, `removed_chat_days = 30` |
| `[admin_cache]` | `ttl_s = 300` |

Starting thresholds are the same for `laya` and `jev`:

| Sensitivity | violation ≥ | suspicion ≥ |
|---|---|---|
| Lenient | 0.95 | 0.75 |
| Balanced (default) | 0.90 | 0.60 |
| Strict | 0.80 | 0.50 |

Signals lower or raise both thresholds. Results are clamped to [0.05, 0.99]:

- **New Member** (first seen < 24 h ago, or < 3 checked messages) whose message contains a link, invite or @mention: −0.10
- Telegram invite link (`t.me/+…`, `t.me/joinchat/…`): −0.05
- Established Member (≥ 30 days in the chat, ≥ 50 checked messages, never flagged): +0.05

## 4. Moderation pipeline

A `message` or `edited_message` update from a **Linked Chat** in `active` status goes through these steps in order. Each step can stop the pipeline.

1. **Exempt Sender filter.** The message is skipped if any of these is true:
   - `from` is an **Admin**. The chat's administrator list is cached for 5 minutes and invalidated by `chat_member` updates.
   - `from.is_bot`.
   - `is_automatic_forward`.
   - `sender_chat` is the chat itself (an anonymous admin).
   - `sender_chat` is the chat's linked channel.
2. **Age filter.** Edits to messages older than 48 hours are skipped, because Telegram no longer allows deleting them.
3. **Text extraction.** The text is `text` or `caption`. URLs from `url` and `text_link` entities are collected separately. Messages with no text, such as a bare photo or a sticker, are skipped.
4. **Short-message filter.** Messages with fewer than `min_words` words and no URL, invite or @mention are skipped as `skipped_short`.
5. **Signals.** Computed from the `member` row and the text (§3). They adjust the thresholds only.
6. **Classification.** The Backend router (§5) returns per-label probabilities, or a skip outcome (`skipped_timeout`, `skipped_overload`, `skipped_unavailable`).
7. **Verdict.** Among the Categories enabled for the chat, pick the one with the highest probability. Disabled Categories are ignored, although the model is always asked about all of them so the question spec stays fixed.
8. **Decision.** `domain.decision` applies the chat's mode, its thresholds and the signals:

| Mode | p ≥ violation | suspicion ≤ p < violation | below |
|---|---|---|---|
| Auto-moderation | **Violation** | **Suspicion** | nothing |
| Observation Mode | **Suspicion** | **Suspicion** | nothing |

9. **Recording.** Every checked message gets a `message_check` row with the Verdict and probabilities but **without text**. Only Suspicions and Violations store the text and entities, in `flagged_message`, which is purged after `flagged_text_days`.

**Edits.** Each edit is checked again from scratch. If the message already has an open Suspicion, a new Suspicion is not re-alerted. A new Violation closes the open Suspicion as `superseded`.

**Messages from other channels** (a `sender_chat` that is neither the chat nor its linked channel) are checked like any other message. On a Violation the bot deletes the message and calls `banChatSenderChat`. There is no Penalty Ladder, no Chat Notice and no Appeal in this case. Admins get an Admin Alert with a 🟢 Unban button.

## 5. Classifier Backends

### Question spec (versioned, `spec_version = 1`)

The same body is sent to Laya and Jev. `state` is `{"message": <text>, "urls": [<url>, …]}`.

```json
{
  "category": {
    "type": "choice",
    "instructions": "You moderate a Telegram group chat. Choose the label that best describes `message` (any language). `urls` lists links found in it.",
    "criteria": {
      "spam":   "Unsolicited bulk or scam content: phishing, crypto or easy-money schemes, 'DM me' bait, mass invites, links dropped without context.",
      "ads":    "Promotion of a product, service, shop, channel, group or referral link, with or without a price or contact.",
      "insult": "Insults, slurs, harassment or demeaning language aimed at a person or group. Swearing not aimed at anyone is not an insult.",
      "clean":  "Ordinary conversation: questions, opinions, jokes, discussion, including mentions of prices, links or products that are not promotion."
    }
  }
}
```

The client reads `answers.category.probabilities` as a label → p map. A response that is missing any of the four labels is treated as a backend error. Changing the spec means increasing `spec_version`, because the thresholds are tuned against a specific spec.

### Client and router

- **`SystemOneClient(base_url, api_key | None, model)`** exposes `classify(state, spec) → Probabilities`. It is built on httpx. Every Jev request carries a pinned `model`. Every Laya request carries `model: "multilingual"`, so that `laya-serve`'s own router never loads the English checkpoint.
- **Backend router** decides which client handles a check for a given chat.
  - Each chat has a `backend` of `laya` or `jev`.
  - A single semaphore across all backends limits concurrent checks to `max_concurrency`. If the semaphore can't be acquired straight away, the check is skipped as `skipped_overload`, never queued.
  - Each call has a timeout of `timeout_s`.
  - **Fallback.** If Jev returns an error (auth, 429, 5xx, timeout) and Laya is deployed and healthy, the router uses Laya and uses the Laya thresholds for that Verdict. If Laya is also unavailable, the check is `skipped_unavailable`.
  - **Incidents.** The first failure on a backend opens a `backend_incident` row. Each affected chat's Admin Alert recipients get one Admin Alert for it, such as "⚠️ Jev is unavailable: authentication failed. Using Laya". The incident closes on the first success, and the bot sends a "✅ Jev is back" follow-up.
- **Availability.**
  - Laya counts as *deployed* if its `/health` answered at least once since the bot started. It counts as *healthy* if the last probe, run every `health_interval_s`, succeeded.
  - Jev counts as *available* if `JEV_API_KEY` is set.
  - A backend that is not available shows as a `disabled` button in the Menu.
  - If the chosen backend can never be available, for example Laya is not deployed and no Jev key is set, the chat's status screen says so.

### Laya server image

This is a multi-stage build:

1. **Builder stage.**
   - Installs CPU torch and `laya[serve,onnx]==0.3.21`, plus the pinned Laya repo checkout that contains `scripts/export_onnx.py`.
   - Downloads `convaiinnovations/laya` at a pinned revision (`LAYA_REVISION` build arg, currently `55cf4c4e…`).
   - Exports `multilingual/` to ONNX with `--quantize`.
2. **Runtime stage.**
   - Keeps the INT8 `.onnx` file, the config and the tokenizer.
   - `app.py` builds `ONNXAgent(onnx_path=…, subfolder="multilingual")`, attaches it to a `Router` as `multilingual`, and serves `laya.serve.create_app(router=…)` on port 8000.
   - The runtime stage needs the CPU torch wheel (verified, see ADR-0002): `laya.common` imports torch at module level and `laya.onnx_agent` imports `laya.common`, so the ONNX path does not work without it. The CPU wheel is installed before `laya[serve,onnx]`, so pip keeps it instead of resolving the CUDA-stacked default.

`laya-serve`'s `/health` is used as the Compose healthcheck. No port is published, and `LAYA_API_KEY` is not set, because the service is reachable only on the internal network.

## 6. Penalties

### Penalty Ladder

- The ladder is a list of 1–10 **Steps**. Each Step is a duration from the presets `5m, 15m, 1h, 3h, 12h, 1d, 3d, 7d, 30d, forever`.
- The default ladder is `[1h, 1d, forever]`, with an Expiry of 30 days.
- The Expiry presets are `7d, 14d, 30d, 60d, 90d, never`.

### Recording a Violation

When a Violation is recorded, in one transaction:

1. `active = count(violations where member, chat, not revoked, expires_at > now) + 1`.
2. `step = ladder[min(active, len(ladder)) − 1]`.
3. Insert the violation with `expires_at = now + expiry`, or no expiry if Expiry is `never`.

Changing the ladder never touches existing violations. It only changes which Step the next Violation gets.

### Restriction

`restrictChatMember` is called with every `can_*` permission set to `False` and `use_independent_chat_permissions=True`.

- `until_date = now + duration` for timed Steps.
- `until_date = 0` for `forever`.
- Durations shorter than 30 seconds or longer than 366 days are sent as `forever`, because Telegram treats them that way.

The applied Restriction is stored on the violation.

### Lifting a Restriction

This happens on an Appeal approval or on 🟢 Lift restriction from the Journal.

1. Read the chat's default permissions from `getChat().permissions`.
2. Call `restrictChatMember` with those permissions, so the Member returns to what the chat normally allows rather than getting every permission.
3. Mark the violation revoked, with `revoked_by` and `revoked_at`. It is now a **False Positive** and no longer counts as Active.

### Order of actions on a Violation

1. Delete the message.
2. Record the Violation.
3. Restrict the Member.
4. Enqueue the Chat Notice.
5. Fan out Admin Alerts.

If deletion fails because the message is already gone, the bot continues. If the restriction fails because rights were lost, the chat becomes a **Suspended Chat** (§10).

## 7. Chat Notices

### Queue

Each chat has an in-memory FIFO queue drained by a sender task.

- **Rate limits.** At most `per_second` messages per second and `per_minute` messages per rolling minute. A `429` makes the sender sleep for `retry_after` and then retry.
- **Staleness.** Items older than `max_queue_age_s` are dropped. The violation is marked `notice_dropped`, so no Appeal is possible, and the drop appears in the Admin Alert as "notice not sent (rate limit)".
- **Restarts.** The queue is not persisted. Anything still queued is lost on restart, but the Restriction is already in place.

### Content

The notice text is the chat's Notice Template if one is set, and the default text in the **Chat Language** otherwise.

- **Default, `en`:** `{user}, it looks like your message {reason}. You can't write here for {duration}.`
- **Default, `ru`:** `{user}, кажется, ваше сообщение {reason}. Вы не можете писать в чат {duration}.`

The notice carries one inline button:

- `🙋 It's a mistake` (`en`) or `🙋 Это ошибка` (`ru`), in the Chat Language.
- Its callback data holds the violation id.
- The button is left out when any of these is true:
  - no Admin has Appeals enabled (§8);
  - the notice was dropped from the queue;
  - the sender was a channel.

### Removal

The notice is deleted at the earliest of:

- when the Restriction ends;
- `max_lifetime_h` after posting, which is the only timer for `forever` Restrictions;
- `outcome_visible_s` after an Appeal has been resolved.

This is scheduled through `chat_notice.delete_at`.

## 8. Appeals

1. A user presses `🙋 It's a mistake`. The bot answers the callback query and checks the following. Each check that fails shows a toast in the Chat Language:
   - the user pressing is the restricted Member, otherwise "This button isn't for you";
   - this is the first Appeal for this violation, otherwise "Already sent";
   - the violation is not already revoked.
2. An `appeal` row is created with status `pending`. The notice button is replaced by the text "⏳ Appeal sent to admins", which is done with `editMessageReplyMarkup` plus a text edit.
3. An Admin Alert (§9) goes to every Admin whose alert mode includes Appeals. The deleted message is quoted with `blockquote` formatting, using its stored entities, and the alert has two buttons:
   - 🟢 **Lift restriction**
   - 🔴 **Reject**
4. The first Admin to press a button decides the outcome.
   - **Approve** lifts the Restriction and marks the violation as a False Positive (§6). The notice is edited to "✅ Restriction lifted by an admin".
   - **Reject** edits the notice to "❌ Appeal rejected".
   - In both cases every other Admin Alert copy is edited to show the outcome and who decided, and the notice is scheduled for removal (§7).
5. The deleted message is never restored.

If the stored text has already been purged because it passed retention, the alert says "message text no longer stored".

## 9. Admin Alerts

Admin Alerts are standalone private messages, not part of the Menu. Each Admin has an alert mode per chat:

| Mode | Receives |
|---|---|
| `all` (default for the Linker) | Violations, Suspicions, Appeals, incidents, Suspension |
| `appeals` | Appeals, incidents, Suspension |
| `off` (default for other Admins) | nothing |

Any Admin can opt in from the chat's settings. If an Admin tries to switch off the last Appeal-receiving recipient, the bot first warns: "Members won't be able to appeal".

An alert can reach an Admin only if that Admin has started the bot. A `403` marks the Admin as unreachable and skips them.

| Alert | Content | Buttons |
|---|---|---|
| Violation | Chat, Member, Category, confidence, Step, and the quoted message | 🟢 Lift restriction |
| Suspicion | Chat, Member, Category, confidence, the quoted message, and a link to it | 🔴 Punish · Dismiss |
| Appeal | As §8 | 🟢 Lift restriction · 🔴 Reject |
| Burst summary | "N violations in the last minute in {chat}", covering at most one alert per chat per minute | Open journal |
| Backend incident / recovery | Backend and reason | — |
| Suspended / removed | What is missing | 🔵 Check again |
| Observation summary | Counts from the last 48 hours, and how many Suspicions the Admin punished | 🟢 Enable auto-moderation |

**Burst handling.**
- The first 5 Violation alerts per chat per minute are sent one by one.
- After that, a single summary alert covers the rest of that minute. Restrictions and deletions carry on regardless.
- Every alert is also paced by the private-chat limit of about 1 message per second per Admin.

**Fan-out and first-click-wins.**
- The bot records every alert message it sends in `admin_alert`, with `admin_id`, `message_id`, `subject_type` and `subject_id`.
- On a decision, a conditional update (`UPDATE … WHERE status = 'pending'`) guarantees that only the first click takes effect.
- The bot then edits every copy of the alert to show the outcome and the deciding Admin, and removes the buttons.
- A late click gets a toast: "Already decided by @admin".

**Punishing a Suspicion.**
- **🔴 Punish** in Auto-moderation or Observation Mode applies a full Violation, with deletion, Restriction and a Chat Notice.
- If the message is older than 48 hours, the deletion is skipped and the alert says so.
- If the Member has since been restricted by another Violation, the new Violation still counts.
- **Dismiss** closes the Suspicion.
- A Suspicion nobody acts on auto-closes as `expired` after `auto_close_h`.

## 10. Linking and chat lifecycle

### Primary path: deep link

1. The Admin presses 🟢 Add to chat. It opens `https://t.me/<bot>?startgroup=<token>&admin=delete_messages+restrict_members`.
   - `<token>` is a random one-time `link_intent` code valid for 1 hour, tied to the Admin who pressed the button.
   - Telegram shows a group picker with the admin rights pre-filled.
2. The bot receives `my_chat_member` (the bot's status changed to administrator) with `from`, the person who performed the action.
3. Checks, in this order:
   1. The chat is a supergroup. If it isn't, the bot tells the Admin that basic groups need to be upgraded, which happens automatically when chat history is made visible or the chat gets a public link.
   2. The bot is an administrator with `can_delete_messages` and `can_restrict_members`.
   3. `from` is `creator` or `administrator` in the chat, checked with `getChatMember`.
4. On success:
   - The chat is inserted, or re-activated if it's inside the 30-day retention window, with status `active`, the defaults (§12) and `linker_id = from.id`.
   - The Linker's alert mode is set to `all`.
   - The Linker's Menu is edited to "✅ {chat} linked" and then the Auto-moderation choice screen.
5. On failure, the Admin's Menu is edited to list exactly what is missing, with a 🔵 Check again button.
6. If `from` has never started the bot, it can't be messaged. In that case the bot posts one message in the group asking that person to open the bot. The message deletes itself after 10 minutes. Linking completes once they press /start.

### Fallback path

This covers a bot that was added manually, or a deep link that expired.

1. On the Add a chat screen, the Admin taps "I added the bot already".
2. The Admin sends the chat's @username, numeric ID (`-100…`), or a message forwarded from the chat.
3. The bot runs the same checks as step 3 of the primary path, with the sender taking the place of `from`.

### Status transitions

Status transitions are driven by `my_chat_member` updates:

| From | Event | To | Effect |
|---|---|---|---|
| active | The bot loses a required right, or is demoted | `suspended` | Checks stop. The Linker gets an alert with 🔵 Check again. |
| suspended | Rights are restored (`my_chat_member`), or Check again succeeds | `active` | The Linker is notified. |
| any | The bot is removed or banned | `removed` | `removed_at` is set. After `removed_chat_days` the chat and all its rows are deleted. |
| removed | The bot is re-added within the window | `active` | The previous settings are restored. |

### Admin access

- Any current Telegram administrator or creator of a chat can open that chat in the Menu.
- Access is checked with `getChatMember` whenever the chat's screens are opened. The result is cached for `admin_cache.ttl_s`.
- Someone who is no longer an Admin loses access immediately on the next check. Their alert rows are kept, but alerts stop being sent to them.

## 11. Scheduler

A single asyncio loop runs every 30 seconds. It is safe across restarts because each job is a due-timestamp query:

| Job | Query |
|---|---|
| Remove Chat Notices | `chat_notice.delete_at <= now AND deleted_at IS NULL` |
| Auto-close Suspicions | `suspicion.status = 'pending' AND created_at <= now - auto_close_h` |
| Observation summary | `chat.mode = 'observation' AND observation_summary_at <= now AND summary_sent = false` |
| Purge flagged text | `flagged_message.purge_at <= now` |
| Delete removed chats | `chat.status = 'removed' AND removed_at <= now - removed_chat_days` |
| Expire link intents | `link_intent.expires_at <= now` |

The bot runs as a single instance. If more instances were ever run, `SELECT … FOR UPDATE SKIP LOCKED` would make the jobs safe to run concurrently.

## 12. Data model (PostgreSQL)

Every chat-scoped table carries a `chat_id` foreign key with `ON DELETE CASCADE`, so the data is ready for multiple tenants (ADR-0001). All timestamps are `timestamptz`.

| Table | Key columns |
|---|---|
| `bot_user` | `user_id` PK, `language` (`en`\|`ru`), `menu_message_id`, `started_at`, `reachable` |
| `chat` | `chat_id` PK, `title`, `status` (`active`\|`suspended`\|`removed`), `mode` (`observation`\|`auto`), `backend` (`laya`\|`jev`), `sensitivity` (`lenient`\|`balanced`\|`strict`), `chat_language`, `ladder` (int[] seconds, 0 = forever), `expiry_seconds` (null = never), `linker_id`, `linked_at`, `observation_summary_at`, `summary_sent`, `removed_at` |
| `category` | `code` PK (`spam`, `ads`, `insult`), `builtin` bool. Seeded. Rows for custom Categories are reserved for later. |
| `chat_category` | (`chat_id`, `category_code`) PK, `enabled`, `violation_threshold`, `suspicion_threshold` (both nullable: null means use the Sensitivity preset; per-Category overrides are reserved for later) |
| `notice_template` | `chat_id` PK, `text`, `entities` (jsonb, Telegram `MessageEntity[]`), `updated_by`, `updated_at` |
| `admin_subscription` | (`chat_id`, `user_id`) PK, `alert_mode` (`all`\|`appeals`\|`off`) |
| `member` | (`chat_id`, `user_id`) PK, `first_seen_at`, `checked_count`, `flagged_count` |
| `message_check` | `id`, `chat_id`, `user_id` \| `sender_chat_id`, `message_id`, `is_edit`, `backend`, `model`, `spec_version`, `outcome` (`clean`\|`suspicion`\|`violation`\|`skipped_short`\|`skipped_timeout`\|`skipped_overload`\|`skipped_unavailable`), `category`, `confidence`, `probabilities` jsonb, `latency_ms`, `created_at`. **No text.** |
| `flagged_message` | `check_id` PK/FK, `text`, `entities` jsonb, `purge_at` |
| `suspicion` | `id`, `check_id`, `chat_id`, `user_id`, `message_id`, `status` (`pending`\|`punished`\|`dismissed`\|`expired`\|`superseded`), `decided_by`, `decided_at` |
| `violation` | `id`, `chat_id`, `user_id` \| `sender_chat_id`, `check_id`, `category`, `source` (`auto`\|`admin`), `step_index`, `restriction_seconds` (0 = forever, null = sender-chat ban), `restricted_until`, `expires_at`, `revoked_at`, `revoked_by`, `notice_dropped`, `created_at` |
| `chat_notice` | `violation_id` PK, `message_id`, `delete_at`, `deleted_at` |
| `appeal` | `id`, `violation_id` UNIQUE, `status` (`pending`\|`approved`\|`rejected`), `decided_by`, `created_at`, `decided_at` |
| `admin_alert` | `id`, `chat_id`, `admin_id`, `message_id`, `subject_type` (`violation`\|`suspicion`\|`appeal`\|…), `subject_id` |
| `backend_incident` | `id`, `backend`, `reason`, `opened_at`, `closed_at` |
| `link_intent` | `token` PK, `user_id`, `expires_at` |
| `fsm_state` | (`bot_id`, `chat_id`, `user_id`, `thread_id`, `destiny`) PK, `state`, `data` jsonb |

Indexes:

- `violation(chat_id, user_id, expires_at) WHERE revoked_at IS NULL`, for counting Active Violations.
- `message_check(chat_id, created_at)`, for Statistics.
- `violation(chat_id, created_at DESC)`, for the Journal.
- A partial index for each scheduler query.

**Defaults on Linking:**

| Setting | Default |
|---|---|
| `mode` | `observation` |
| `backend` | `laya` if Laya is deployed, otherwise `jev` |
| `sensitivity` | `balanced` |
| `chat_language` | the Linker's interface language |
| `ladder` | `[3600, 86400, 0]` |
| `expiry_seconds` | 30 days |
| Categories | all enabled |

## 13. Menu

The Menu is one message per Admin, and its id is stored as `bot_user.menu_message_id`. Every screen change edits that message with `editMessageText` and new `reply_markup`. `/start` works as follows:

- **No language set yet:** show the language screen.
- **Otherwise:** try to edit the stored Menu message to the Home screen. If it can't be edited any more (deleted, or too old), send a new one and delete the old one if possible.

The bot's own prompt messages are never left behind. Free-text input from the Admin (a Notice Template, a chat ID) is read and the Admin's message is then deleted. The one exception is the forwarded message in the fallback Linking path.

**Button colours:**
- 🟢 `success` for confirming or safe actions (Save, Lift restriction, Enable).
- 🔴 `danger` for punishing or destructive actions (Punish, Reject, Reset, Remove step).
- 🔵 `primary` for the main action on a screen.
- Everything else has no style. There is at most one button of each style per screen.
- No custom emoji are used anywhere.

Unavailable options use `disabled` buttons (Bot API 10.3).

**Screen map:**

```
Language ──► Home
Home
 ├─ [chat …]  (one button per chat)  ──► Chat
 ├─ 🔵 Add to chat ──► Add a chat ── deep link ── "I added the bot already" ──► Enter chat ID
 ├─ Language
 └─ How it works
Chat  (status: mode · backend · Sensitivity · suspended/ok)
 ├─ Settings
 │   ├─ Mode: 🟢 Enable auto-moderation  /  Switch to observation
 │   ├─ Categories (toggle spam / ads / insult)
 │   ├─ Sensitivity (Lenient / Balanced / Strict)
 │   ├─ Penalty Ladder ──► Step N ──► duration presets; ➕ Add step; 🔴 Remove last; Expiry presets
 │   ├─ Chat Language (🇷🇺 / 🇬🇧)
 │   ├─ Notice Template ──► show current · Edit (send text) ──► Preview ──► 🟢 Save / Cancel · 🔴 Reset to default
 │   ├─ Classifier Backend (Laya / Jev; unavailable = disabled)
 │   └─ My alerts (All / Appeals only / Off)
 ├─ Statistics (7d / 30d toggle)
 └─ Journal (5 per page ◀ ▶) ──► Violation card ──► 🟢 Lift restriction
```

- **Callback data** uses aiogram `CallbackData` factories. Every one includes the `chat_id` so the admin check can run, and some also include page or entity ids.
- **Every callback re-checks Admin access** for the chat it refers to. A stale callback shows a toast and the Home screen.
- **Language screen.** It shows `🇷🇺 Русский` and `🇬🇧 English`. The option matching Telegram's `language_code` gets `primary`, and neither does if the code is something else.
- **Auto-moderation choice** (shown right after Linking):
  - 🟢 Enable auto-moderation now;
  - 🔵 Observe for 2 days first, which sets `observation_summary_at = now + 48h`.

  The Observation summary alert repeats the offer once. After that the chat stays in Observation Mode until the Admin changes it.
- **Statistics.** Counts for the last 7 or 30 days: messages checked, Violations per Category, Suspicions, Appeals, and False Positives.

## 14. Notice Templates

### Input

1. The Admin taps **Edit** and sends a formatted message.
2. The bot keeps its `text` and `entities` as received. This is lossless: bold, italic, underline, strikethrough, spoiler, code, pre, links, blockquote and expandable blockquote all survive.
3. `custom_emoji` entities are removed. Their fallback emoji characters are already in `text`.

### Placeholders

| Placeholder | Rendered as |
|---|---|
| `{user}` | A `text_mention` of the Member's display name, so it works without a username |
| `{reason}` | The Category phrase in the Chat Language (`en`: "looks like spam" / "looks like advertising" / "contains insults"; `ru`: equivalents that fit the sentence) |
| `{duration}` | The Step in the Chat Language, with correct plural forms from Fluent (`1 час`, `3 часа`, `5 часов`; "forever" / "навсегда") |
| `{strike}` | `N/M`: the number of Active Violations over the ladder length. Capped at `M/M` once the last Step repeats. |

### Validation

Validation runs before the preview is shown:

- Any `{…}` whose name isn't in the table above is an error. The bot lists the allowed names and highlights the unknown one.
- `{{` and `}}` are literal braces.
- If `{user}` is missing, the bot warns but still allows saving.
- The worst-case render, using the longest name and phrases, must fit in 1024 characters.

### Rendering

Rendering is a pure function:

```
render(text, entities, values) → (text, entities)
```

- It replaces placeholders from left to right.
- Every entity offset after a replacement moves by the length difference in **UTF-16 code units**, which is what Telegram counts.
- An entity that fully contains a placeholder stretches to cover the new value.
- The `{user}` value adds its own `text_mention` entity.

### Preview and saving

- The preview is sent inside the Menu, rendered with sample values in the Chat Language.
- The Admin then chooses 🟢 Save, Cancel, or 🔴 Reset to default.
- One template applies per chat. Placeholder values always follow the Chat Language, and the Appeal button language does too.

## 15. Internationalisation

- aiogram-i18n with the Fluent core. Locales are `en` and `ru`, and each has `menu.ftl`, `alerts.ftl` and `notices.ftl`.
- The locale depends on where the text is shown:
  - **Private chat** (Menu, Admin Alerts) uses `bot_user.language`.
  - **Group chat** (Chat Notices, Appeal button, toasts shown to Members) uses `chat.chat_language`.
- A middleware picks the locale from the update's chat type. Code that renders a group text asks for the chat's locale explicitly.
- Adding a language means adding a locale folder and a button on the language screens. No other code changes.

## 16. Deployment

`docker-compose.yml` defines these services:

| Service | Settings |
|---|---|
| `postgres` | Postgres 17. Volume `pgdata`, `pg_isready` healthcheck. |
| `laya` | Profile `laya`. Built from `src/laya-server`, `/health` healthcheck, 2–3 GB memory limit. |
| `bot` | Built from `src/bot`. Runs `alembic upgrade head` on start, then starts polling. Depends on a healthy `postgres`, and on `laya` when it is enabled. |

- All services share an internal network, and no ports are published.
- The bot uses long polling with `allowed_updates = message, edited_message, callback_query, my_chat_member, chat_member`.

Quick start:

```bash
cp .env.example .env    # set BOT_TOKEN and POSTGRES_PASSWORD
docker compose up -d --build
```

The bot must be an administrator in each Linked Chat, since admins receive every message. It does not need privacy mode switched off.

## 17. Testing

| Layer | Seam | What it covers |
|---|---|---|
| Unit | `domain/` pure functions | Step selection, Expiry, ladder edits, repeat-last-Step, Decision table, signal adjustments and clamping, template validation, rendering with UTF-16 offsets and entity shifting, plural forms |
| Unit | `SystemOneClient` against an httpx mock transport with recorded JSON fixtures (Laya and Jev responses) | Request body, including the pinned `model`; probability extraction; error mapping; timeouts |
| Integration | Repositories against Postgres in testcontainers | Active Violation counting, first-click-wins conditional updates, scheduler queries, cascade delete |
| End-to-end | aiogram `Dispatcher.feed_update` with fabricated `Update`s, a recording fake bot session, `FakeBackend` with scripted probabilities, `FakeClock`, and Postgres in testcontainers | Linking via `my_chat_member` and the fallback path; the full Violation → Chat Notice → Appeal → approve/reject flow; Suspicion punish, dismiss and auto-close; Observation Mode and its summary; Menu navigation editing one message; Notice Template save and render; Jev → Laya fallback and incident alerts; Suspension and re-activation; Chat Notice rate limiting and dropping |

- Tests assert on what the bot does to the outside world: the Bot API calls it records and the DB state it leaves behind. They never assert on internal calls.
- CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check` and `pytest` on every push and PR.
- Building Docker images and a real Laya smoke test are on the ROADMAP.

# Laya Moderator

A self-hosted Telegram bot that checks every message in its linked chats with an AI classifier and restricts members who break the chat's rules.

## Language

### Instance and people

**Instance**:
One deployed copy of the bot, running with a single Telegram bot token.
_Avoid_: server, deployment, bot (when you mean the deployment)

**Operator**:
The person who deploys and maintains an Instance and owns its `.env`.
_Avoid_: bot owner, host

**Admin**:
A Telegram user who is currently an administrator or creator of a Linked Chat and has started the bot in private. Admin status always comes from Telegram; the bot keeps no roles of its own.
_Avoid_: owner, moderator, chat owner

**Linker**:
The Admin who linked a chat. By default, the Linker is the one who receives that chat's Admin Alerts.
_Avoid_: owner

**Member**:
A user who writes in a Linked Chat and whose messages are subject to moderation.
_Avoid_: user (ambiguous: an Admin in the bot's private chat is also a user)

**Exempt Sender**:
A sender whose messages are never checked: Admins, bots, and automatic forwards from the linked channel.
_Avoid_: whitelisted, trusted

### Chats

**Linked Chat**:
A Telegram supergroup in which the bot is an administrator with the required rights, and which is attached to the Instance through Linking.
_Avoid_: group, channel, room

**Linking**:
The process that makes a chat a Linked Chat. The bot receives its rights and checks that the person linking the chat is an Admin of it.
_Avoid_: registration, binding

**Suspended Chat**:
A Linked Chat in which the bot has lost the rights it needs. No messages are checked until the rights are restored.
_Avoid_: disabled, paused

**Observation Mode**:
A Linked Chat mode in which the bot deletes nothing and restricts nobody. Every Verdict above the Suspicion threshold is sent to Admins as a Suspicion instead. Every new chat starts in this mode.
_Avoid_: test mode, dry run

**Auto-moderation**:
A Linked Chat mode in which the bot records Violations and applies Restrictions on its own.
_Avoid_: live mode, production mode

### Checking messages

**Category**:
A kind of unwanted content the bot recognises: spam, advertising, insult. Each one can be switched on or off per chat.
_Avoid_: filter, tag, reason

**Classifier Backend**:
The model that produces Verdicts for a Linked Chat, either the local Laya or the external Jev. It is chosen per chat.
_Avoid_: provider, engine, AI

**Verdict**:
The outcome of checking one message: a Category (or "clean") together with a confidence.
_Avoid_: detection, score, prediction

**Sensitivity**:
A Linked Chat setting: Lenient, Balanced, or Strict. Each value maps to a pair of confidence thresholds, one for Violations and one for Suspicions.
_Avoid_: threshold, strictness

**Minimum length**:
A Linked Chat setting: link-free messages shorter than this many characters are not checked at all. Off (0) checks every message. Characters are counted the way Telegram counts them.
_Avoid_: min_words, word limit, message limit

**Suspicion**:
A message whose Verdict falls in the middle confidence band. The message stays in the chat, and an Admin decides whether to Punish or Dismiss it.
_Avoid_: warning, flag

**Violation**:
A confirmed case of a Member's message matching an enabled Category. It is confirmed either automatically at high confidence or by an Admin punishing a Suspicion. The message is deleted, and the Violation counts on the Penalty Ladder.
_Avoid_: strike, ban, incident, detection

**False Positive**:
A Violation that an Admin has revoked. It no longer counts on the Penalty Ladder.
_Avoid_: bot error

### Penalties

**Restriction**:
A ban on a Member sending messages in a chat, either for a period of time or forever. The Member stays in the chat.
_Avoid_: ban, mute, block, kick

**Penalty Ladder**:
The ordered sequence of Steps configured for each chat. The default is 1 hour → 24 hours → forever.
_Avoid_: escalation, levels

**Step**:
One item on the Penalty Ladder: the length of a Restriction. The Step applied is determined by how many Active Violations the Member has in that chat. When a Member has more Active Violations than there are Steps, the last Step repeats.
_Avoid_: level, tier

**Active Violation**:
A Violation that has not expired and has not been revoked.
_Avoid_: active strike

**Expiry**:
The point after which a Violation stops being Active. It is counted separately for each Violation, starting from when that Violation was recorded. The period is set per chat and defaults to 30 days. Expiry never lifts a Restriction that has already been applied.
_Avoid_: reset, amnesty

### Communication

**Chat Notice**:
The bot's message in a Linked Chat announcing a Member's Violation and Restriction, carrying the Appeal button. It is removed when the Restriction ends or the Appeal is resolved.
_Avoid_: warning, alert

**Notice Template**:
An Admin-defined Chat Notice text with formatting and placeholders. Each chat has at most one. When none is set, the default text in the Chat Language is used.
_Avoid_: custom message

**Chat Language**:
The language the bot uses inside a Linked Chat: Chat Notices, placeholder values, and buttons. It is set separately from the Admin's interface language.
_Avoid_: chat locale

**Admin Alert**:
A private message to an Admin about a Violation, a Suspicion, or an Appeal, with action buttons. It is separate from the Menu.
_Avoid_: notice (that word means Chat Notice), notification

**Menu**:
The single interface message in an Admin's private chat with the bot. It is edited in place to move between screens.
_Avoid_: panel, dashboard

**Journal**:
The Menu's list of a chat's Violations, newest first, five to a page. Each entry opens the Violation card.
_Avoid_: log, history

**Statistics**:
The Menu's counts of one chat's checks, Violations, Suspicions, Appeals and False Positives over the last 7 or 30 days.
_Avoid_: analytics, metrics

**Violation card**:
A Journal entry opened: the Violation with its Category, confidence, Step, moment and current state, quoting the flagged message while the retention keeps it. Its Lift restriction is the Admin Alert action itself.
_Avoid_: details, profile

**Appeal**:
A Member's request to lift a Restriction. The Member files it with the "It's a mistake" button on the Chat Notice, and can file one per Violation. The deleted message stays deleted whatever the outcome.
_Avoid_: complaint, report, dispute

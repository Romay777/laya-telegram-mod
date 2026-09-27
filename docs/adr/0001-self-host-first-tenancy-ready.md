# Self-host first, with a schema ready for multi-tenancy

v1 ships as self-host only: one Instance, one bot token, one Operator. Even so, all data is keyed by Linked Chat from the start, and Admin rights come from Telegram rather than from Instance configuration. A future public service can then grow out of the same codebase without migrating data or adding a role model of its own, because chats are already independent of each other.

## Consequences

- There is no global "Instance admin" who manages chats. The Operator controls only the deployment and the `.env`.
- Secrets live with the Operator: the Jev API key is set in `.env` and shared by every chat on the Instance. Per-chat keys are only needed for a public Instance (see ROADMAP).
- Classifier load grows with the number of chats on an Instance. A public service will need to scale the classifier out horizontally.

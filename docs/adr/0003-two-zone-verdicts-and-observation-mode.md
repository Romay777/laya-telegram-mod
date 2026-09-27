# Two-zone Verdicts, with new chats starting in Observation Mode

Without fine-tuning, Laya is barely above chance on its authors' held-out toxicity benchmark (accuracy ≈ 0.53), and it is overconfident out of the box. So the bot does not punish on a single threshold:

- High-confidence Verdicts become Violations.
- Mid-confidence Verdicts become Suspicions for an Admin to Punish or Dismiss.

Every new chat starts in Observation Mode. The Admin switches on Auto-moderation, right away or after a two-day summary.

## Consequences

- Admins get more Admin Alerts early on. This is the deliberate price for fewer wrongful Restrictions.
- Cheap deterministic signals are computed before the model runs: links, invite links, a new Member, very short messages. They shift the thresholds but never punish on their own.
- The starting thresholds are not calibrated. Calibrating them against an eval set is on the ROADMAP.

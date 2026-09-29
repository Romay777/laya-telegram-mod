alert-violation-header = ⚠️ {$chat}
alert-violation-member = Member: {$member}
alert-violation-verdict = {$category}, {$confidence}%
alert-violation-step = Restriction: {$duration}
category-spam = Spam
category-ads = Advertising
category-insult = Insults
alert-text-not-stored = The message text is no longer stored.
alert-lift-button = 🟢 Lift restriction
alert-lifted = ✅ Restriction lifted by {$admin}
alert-already-decided = Already decided by {$admin}
alert-appeal-line = 🙋 The Member has appealed
alert-reject-button = 🔴 Reject
alert-appeal-rejected = ❌ Appeal rejected by {$admin}
alert-suspicion-header = 🔍 {$chat}
alert-punish-button = 🔴 Punish
alert-dismiss-button = Dismiss
alert-suspicion-punished = 🔴 Punished by {$admin}
alert-suspicion-dismissed = Dismissed by {$admin}
alert-suspicion-not-deleted = The message is older than 48 hours, so it stays in the chat.
alert-suspicion-expired = This suspicion has already expired.
alert-notice-not-sent = Notice not sent (rate limit).
alert-burst-summary = {$count ->
    [one] ⚡ {$count} violation in the last minute in {$chat}
   *[other] ⚡ {$count} violations in the last minute in {$chat}
}
alert-open-journal = Open journal
summary-header = 📊 {$chat} — the last {$hours} hours
summary-suspicions = Suspicions: {$count}
summary-punished = Punished by you: {$count}
alert-incident = ⚠️ {$backend} is unavailable: {$reason}. Using Laya
alert-incident-recovery = ✅ {$backend} is back

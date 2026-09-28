"""The versioned System One question spec for Categories (§5).

One spec for every Classifier Backend: the same body goes to Laya and Jev
(ADR-0002). `state` is `{"message": <text>, "urls": [<url>, …]}`. Changing
the spec means raising SPEC_VERSION, because the Sensitivity thresholds are
tuned against a specific spec.
"""

from typing import Final

#: Tuned against this exact question set (§5).
SPEC_VERSION: Final = 1

#: The labels of the `category` choice; exactly these four, in this order.
LABELS: Final = ("spam", "ads", "insult", "clean")

#: The checkpoint every Laya request pins, so laya-serve's own router never
#: loads the English checkpoint (§5, ADR-0002).
LAYA_MODEL: Final = "multilingual"

#: The internal Compose URL of the Laya service; Compose builds it, so it is
#: not an Operator setting (§3, ADR-0002).
LAYA_BASE_URL: Final = "http://laya:8000/v1"

QUESTION_SPEC: Final = {
    "category": {
        "type": "choice",
        "instructions": (
            "You moderate a Telegram group chat. Choose the label that best describes "
            "`message` (any language). `urls` lists links found in it."
        ),
        "criteria": {
            "spam": (
                "Unsolicited bulk or scam content: phishing, crypto or easy-money schemes, "
                "'DM me' bait, mass invites, links dropped without context."
            ),
            "ads": (
                "Promotion of a product, service, shop, channel, group or referral link, "
                "with or without a price or contact."
            ),
            "insult": (
                "Insults, slurs, harassment or demeaning language aimed at a person or "
                "group. Swearing not aimed at anyone is not an insult."
            ),
            "clean": (
                "Ordinary conversation: questions, opinions, jokes, discussion, including "
                "mentions of prices, links or products that are not promotion."
            ),
        },
    }
}

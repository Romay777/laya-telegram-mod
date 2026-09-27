"""Test doubles and shared constants for the laya-server tests.

The agent layer is faked (a scripted stand-in for `laya.onnx_agent.ONNXAgent`)
so the HTTP contract and router registration are exercised through the real
`laya.serve.create_app` and a real `laya.Router` without loading the real ONNX
graph, exactly as the ticket's pre-agreed seams allow.
"""

# The versioned System One question spec for Categories (ARCHITECTURE §5,
# spec_version = 1). The bot owns the single copy of this; the server test
# inlines it as the contract both sides speak.
QUESTION_SPEC = {
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
                "Insults, slurs, harassment or demeaning language aimed at a person or group. "
                "Swearing not aimed at anyone is not an insult."
            ),
            "clean": (
                "Ordinary conversation: questions, opinions, jokes, discussion, including "
                "mentions of prices, links or products that are not promotion."
            ),
        },
    }
}

# Every label of the question spec (ARCHITECTURE §5).
SPEC_LABELS = ("spam", "ads", "insult", "clean")


class FakeMultilingualAgent:
    """Scripted stand-in for ONNXAgent: answers choice questions with fixed probabilities."""

    model_id = "fake-checkpoint"
    revision = None

    def __init__(self, probabilities: dict[str, float] | None = None):
        self.probabilities = probabilities or {
            "spam": 0.02,
            "ads": 0.03,
            "insult": 0.01,
            "clean": 0.94,
        }

    def system_one(self, state, questions, lang=None, **overrides):
        answers = {}
        for qid, question in questions.items():
            if question.get("type") != "choice":
                raise ValueError(
                    f"FakeMultilingualAgent only answers choice questions: {qid}"
                )
            probabilities = {
                label: self.probabilities[label] for label in question["criteria"]
            }
            answers[qid] = {
                "type": "choice",
                "choice": max(probabilities, key=probabilities.get),
                "probabilities": probabilities,
                "confidence": max(probabilities.values()),
                "answer_confidence": max(probabilities.values()),
                "action": {"act_probability": 0.99},
            }
        return {
            "model": "laya-rl-agent-onnx",
            "answers": answers,
            "usage": {"input_tokens": 16, "output_tokens": 0},
        }

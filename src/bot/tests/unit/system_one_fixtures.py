"""Recorded JSON fixtures for the System One protocol (§17).

The bodies are recorded from the Laya server contract
(`src/laya-server/tests/test_http_contract.py`): a `choice` answer carries
`answers.<question>.probabilities` with every label of the question spec.
"""

from app.classifiers.spec import LABELS

CLEAN_ANSWER = {
    "model": "laya-rl-agent-onnx",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "clean",
            "probabilities": {"spam": 0.02, "ads": 0.03, "insult": 0.01, "clean": 0.94},
            "confidence": 0.94,
        }
    },
    "usage": {"input_tokens": 16, "output_tokens": 0},
}

SPAM_ANSWER = {
    "model": "laya-rl-agent-onnx",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "spam",
            "probabilities": {"spam": 0.97, "ads": 0.01, "insult": 0.01, "clean": 0.01},
            "confidence": 0.97,
        }
    },
    "usage": {"input_tokens": 24, "output_tokens": 0},
}


def without_label(answer: dict, label: str) -> dict:
    """A malformed answer: one label of the question spec is missing (§5)."""
    category = answer["answers"]["category"]
    broken = {
        "answers": {
            "category": {
                key: (dict(value) if isinstance(value, dict) else value)
                for key, value in category.items()
            }
        }
    }
    broken["answers"]["category"]["probabilities"] = {
        name: p for name, p in category["probabilities"].items() if name != label
    }
    return broken


def assert_all_labels(probabilities: dict[str, float]) -> None:
    assert set(probabilities) == set(LABELS)

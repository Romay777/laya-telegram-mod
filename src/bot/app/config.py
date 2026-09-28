"""Settings loading: `.env` plus the `config.toml` shipped in the image (§3).

`.env` holds only what the Operator sets (BOT_TOKEN, POSTGRES_PASSWORD, the
Jev connection, LOG_LEVEL); Compose builds DATABASE_URL from it. The
`config.toml` tunables every value has a default for; Operators normally
never touch the file, and override it by mounting.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The image ships this file next to the app package; override it by mounting.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"

BACKENDS = ("laya", "jev")
SENSITIVITIES = ("lenient", "balanced", "strict")

# §3 starting thresholds, used for any combination missing from config.toml.
_DEFAULT_THRESHOLDS: dict[tuple[str, str], tuple[float, float]] = {
    ("lenient", "violation"): 0.95,
    ("lenient", "suspicion"): 0.75,
    ("balanced", "violation"): 0.90,
    ("balanced", "suspicion"): 0.60,
    ("strict", "violation"): 0.80,
    ("strict", "suspicion"): 0.50,
}


@dataclass(frozen=True)
class SensitivityThresholds:
    violation: float
    suspicion: float


def default_thresholds() -> dict[str, dict[str, SensitivityThresholds]]:
    """The §3 starting thresholds for every backend and Sensitivity."""
    return {
        backend: {
            sensitivity: SensitivityThresholds(
                violation=_DEFAULT_THRESHOLDS[(sensitivity, "violation")],
                suspicion=_DEFAULT_THRESHOLDS[(sensitivity, "suspicion")],
            )
            for sensitivity in SENSITIVITIES
        }
        for backend in BACKENDS
    }


@dataclass(frozen=True)
class SignalsSettings:
    """Cheap deterministic adjustments to both thresholds (§3), clamped later."""

    new_member_link: float = -0.10
    invite_link: float = -0.05
    established_member: float = 0.05


@dataclass(frozen=True)
class ClassifierSettings:
    timeout_s: float = 3
    max_concurrency: int = 4
    health_interval_s: int = 60


@dataclass(frozen=True)
class ModerationSettings:
    min_words: int = 3
    new_member_hours: int = 24
    new_member_messages: int = 3


@dataclass(frozen=True)
class NoticesSettings:
    per_second: float = 1
    per_minute: int = 18
    max_queue_age_s: int = 300
    max_lifetime_h: int = 24
    outcome_visible_s: int = 600


@dataclass(frozen=True)
class SuspicionsSettings:
    auto_close_h: int = 24


@dataclass(frozen=True)
class ObservationSettings:
    summary_after_h: int = 48


@dataclass(frozen=True)
class RetentionSettings:
    flagged_text_days: int = 30
    removed_chat_days: int = 30


@dataclass(frozen=True)
class AdminCacheSettings:
    ttl_s: int = 300


@dataclass(frozen=True)
class LinkingSettings:
    """How long the self-deleting group prompt of a not-yet-started Linker stays (§10)."""

    prompt_delete_after_s: float = 600


@dataclass(frozen=True)
class JevSettings:
    api_key: str | None = None
    base_url: str = "https://api.typesafe.ai/v1"
    model: str = "jev-1.13.0"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    log_level: str = "INFO"
    jev: JevSettings = field(default_factory=JevSettings)
    thresholds: dict[str, dict[str, SensitivityThresholds]] = field(default_factory=dict)
    signals: SignalsSettings = field(default_factory=SignalsSettings)
    classifier: ClassifierSettings = field(default_factory=ClassifierSettings)
    moderation: ModerationSettings = field(default_factory=ModerationSettings)
    notices: NoticesSettings = field(default_factory=NoticesSettings)
    suspicions: SuspicionsSettings = field(default_factory=SuspicionsSettings)
    observation: ObservationSettings = field(default_factory=ObservationSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    admin_cache: AdminCacheSettings = field(default_factory=AdminCacheSettings)
    linking: LinkingSettings = field(default_factory=LinkingSettings)

    @classmethod
    def load(
        cls,
        env: Mapping[str, str] = os.environ,
        config_path: Path | None = None,
    ) -> "Settings":
        """Operator settings from the environment, tunables from config.toml."""
        bot_token = env.get("BOT_TOKEN", "")
        database_url = env.get("DATABASE_URL", "")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set; copy .env.example to .env and fill it in")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set; Compose builds it from POSTGRES_PASSWORD")

        config: dict[str, Any] = {}
        path = config_path or DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("rb") as fp:
                config = tomllib.load(fp)

        thresholds: dict[str, dict[str, SensitivityThresholds]] = {}
        raw_thresholds = config.get("thresholds", {})
        for backend in BACKENDS:
            thresholds[backend] = {}
            for sensitivity in SENSITIVITIES:
                section = raw_thresholds.get(backend, {}).get(sensitivity, {})
                thresholds[backend][sensitivity] = SensitivityThresholds(
                    violation=float(
                        section.get("violation", _DEFAULT_THRESHOLDS[(sensitivity, "violation")])
                    ),
                    suspicion=float(
                        section.get("suspicion", _DEFAULT_THRESHOLDS[(sensitivity, "suspicion")])
                    ),
                )

        jev_api_key = env.get("JEV_API_KEY") or None  # Jev is available only with a key

        return cls(
            bot_token=bot_token,
            database_url=database_url,
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
            jev=JevSettings(
                api_key=jev_api_key,
                base_url=env.get("JEV_BASE_URL", "https://api.typesafe.ai/v1"),
                model=env.get("JEV_MODEL", "jev-1.13.0"),
            ),
            thresholds=thresholds,
            signals=SignalsSettings(**config.get("signals", {})),
            classifier=ClassifierSettings(**config.get("classifier", {})),
            moderation=ModerationSettings(**config.get("moderation", {})),
            notices=NoticesSettings(**config.get("notices", {})),
            suspicions=SuspicionsSettings(**config.get("suspicions", {})),
            observation=ObservationSettings(**config.get("observation", {})),
            retention=RetentionSettings(**config.get("retention", {})),
            admin_cache=AdminCacheSettings(**config.get("admin_cache", {})),
            linking=LinkingSettings(**config.get("linking", {})),
        )

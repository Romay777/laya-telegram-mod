"""Pure config: the §3 Sensitivity presets per backend, loaded from config.toml.

Each preset is a pair of thresholds; the §3 starting values are the same for
laya and jev and are not calibrated.
"""

from pathlib import Path

from app.config import DEFAULT_CONFIG_PATH, Settings, default_thresholds

_ENV = {"BOT_TOKEN": "42:test", "DATABASE_URL": "postgresql+asyncpg://x/y"}


def _write(tmp_path: Path, toml_text: str) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(toml_text)
    return config


def thresholds_from(tmp_path: Path, toml_text: str) -> dict[str, dict[str, tuple[float, float]]]:
    config = tmp_path / "config.toml"
    config.write_text(toml_text)
    settings = Settings.load(env=_ENV, config_path=config)
    return {
        backend: {
            sensitivity: (preset.violation, preset.suspicion)
            for sensitivity, preset in presets.items()
        }
        for backend, presets in settings.thresholds.items()
    }


def test_the_shipped_config_toml_carries_the_three_section_three_presets() -> None:
    settings = Settings.load(env=_ENV, config_path=DEFAULT_CONFIG_PATH)

    assert settings.thresholds["laya"]["balanced"].violation == 0.90
    assert settings.thresholds["laya"]["balanced"].suspicion == 0.60


def test_a_config_from_before_the_min_chars_move_still_loads(tmp_path: Path) -> None:
    """`min_words` moved into each chat (§13); a mounted copy must not block startup."""
    config = _write(tmp_path, "[moderation]\nmin_words = 3\nnew_member_hours = 12\n")

    settings = Settings.load(env=_ENV, config_path=config)

    assert settings.moderation.new_member_hours == 12  # the valid keys stand


def test_every_preset_of_the_starting_values_per_backend_and_sensitivity() -> None:
    thresholds = default_thresholds()

    assert thresholds["laya"]["lenient"].violation == 0.95
    assert thresholds["laya"]["lenient"].suspicion == 0.75
    assert thresholds["laya"]["balanced"].violation == 0.90
    assert thresholds["laya"]["balanced"].suspicion == 0.60
    assert thresholds["laya"]["strict"].violation == 0.80
    assert thresholds["laya"]["strict"].suspicion == 0.50
    assert thresholds["jev"] == thresholds["laya"]  # §3: the same starting values


def test_a_config_toml_override_replaces_the_preset_for_one_combination(
    tmp_path: Path,
) -> None:
    thresholds = thresholds_from(
        tmp_path,
        "[thresholds.jev.strict]\nviolation = 0.7\nsuspicion = 0.4\n",
    )

    assert thresholds["jev"]["strict"] == (0.7, 0.4)
    assert thresholds["jev"]["balanced"] == (0.90, 0.60)  # the others keep the defaults
    assert thresholds["laya"]["strict"] == (0.80, 0.50)


def test_a_combination_missing_from_config_toml_falls_back_to_the_defaults(
    tmp_path: Path,
) -> None:
    thresholds = thresholds_from(tmp_path, "")

    assert thresholds["laya"]["lenient"] == (0.95, 0.75)
    assert thresholds["jev"]["balanced"] == (0.90, 0.60)


def test_the_shipped_signal_shifts_are_the_section_three_values() -> None:
    """All §3 shift values come from config (issue #15): defaults and override."""
    settings = Settings.load(env=_ENV, config_path=DEFAULT_CONFIG_PATH)
    assert settings.signals.new_member_link == -0.10
    assert settings.signals.invite_link == -0.05
    assert settings.signals.established_member == 0.05

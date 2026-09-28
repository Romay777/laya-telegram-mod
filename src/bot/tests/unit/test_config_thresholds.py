"""Pure config: the §3 Sensitivity presets per backend, loaded from config.toml.

Each preset is a pair of thresholds; the §3 starting values are the same for
laya and jev and are not calibrated.
"""

from pathlib import Path

from app.config import DEFAULT_CONFIG_PATH, Settings, default_thresholds

_ENV = {"BOT_TOKEN": "42:test", "DATABASE_URL": "postgresql+asyncpg://x/y"}


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

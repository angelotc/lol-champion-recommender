import tomllib
from pathlib import Path

import pytest

from desktop import settings_ui as su
from config import DEFAULT_COMFORT, Config, load_config

SHIPPED = Path(__file__).resolve().parent.parent / "config.toml"


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    return p


def test_round_trip_keeps_comments(cfg_path):
    before = cfg_path.read_text(encoding="utf-8")
    v = su.read_values(cfg_path) | {"min_pick_rate": 0.5, "comfort_only": True, "comfort_weight": 1.5}
    assert su.save(v, cfg_path) == {}
    after = cfg_path.read_text(encoding="utf-8")
    for line in before.splitlines():
        if line.startswith("#"):
            assert line in after
    assert "Shrinkage constant" in after
    cfg = load_config(cfg_path)
    assert (cfg.min_pick_rate, cfg.comfort_only) == (0.5, True)
    assert cfg.comfort == {"weight": 1.5}
    assert su.read_values(cfg_path) == v
    assert cfg.lockfile == Config().lockfile  # literal string kept intact


def test_untouched_keys_survive(cfg_path):
    cfg_path.write_text(cfg_path.read_text(encoding="utf-8") + '\n[weights.support]\nbase = 2.0\n', encoding="utf-8")
    su.save(su.read_values(cfg_path) | {"comfort_only": True}, cfg_path)
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw["weights"]["support"]["base"] == 2.0
    assert raw["comfort_only"] is True and raw["tier"] == "master_plus"


def test_comfort_table_created_only_when_needed(cfg_path):
    v = su.read_values(cfg_path)
    assert "comfort" not in tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    su.save(v, cfg_path)  # nothing differs from the defaults
    assert "comfort" not in tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    su.save(v | {"comfort_weight": 0}, cfg_path)
    assert tomllib.loads(cfg_path.read_text(encoding="utf-8"))["comfort"]["weight"] == 0
    assert load_config(cfg_path).weights_for("support")["comfort"]["weight"] == 0


@pytest.mark.parametrize("field,value", [
    ("min_pick_rate", 6), ("min_pick_rate", -1), ("min_pick_rate", None), ("min_pick_rate", "abc"),
    ("comfort_weight", 3), ("lockfile", "  "), ("comfort_only", 1),
])
def test_validation_rejects(cfg_path, field, value):
    before = cfg_path.read_text(encoding="utf-8")
    v = su.read_values(cfg_path) | {field: value}
    assert field in su.validate(v)
    assert field in su.save(v, cfg_path)
    assert cfg_path.read_text(encoding="utf-8") == before


def test_defaults_match_config():
    d, c = su.defaults(), Config()
    assert su.validate(d) == {}
    assert (d["min_pick_rate"], d["comfort_only"], d["lockfile"]) == (c.min_pick_rate, c.comfort_only, c.lockfile)
    assert d["comfort_weight"] == DEFAULT_COMFORT["weight"]


def test_options_not_in_the_window_survive_a_save(cfg_path):
    cfg_path.write_text(cfg_path.read_text(encoding="utf-8").replace("top_n = 8", "top_n = 12")
                        + "\n[comfort]\nhalf_life = 30\n", encoding="utf-8")
    su.save(su.read_values(cfg_path) | {"comfort_only": True}, cfg_path)
    cfg = load_config(cfg_path)
    assert cfg.top_n == 12 and cfg.comfort["half_life"] == 30 and cfg.comfort_only


def test_api_save_calls_back(cfg_path):
    got = []
    api = su.SettingsApi(cfg_path, got.append)
    assert api.save(api.get()["values"] | {"min_pick_rate": 2.0}) == {"ok": True, "errors": {}}
    assert got[0].min_pick_rate == 2.0
    assert api.save(api.get()["values"] | {"min_pick_rate": 99})["ok"] is False and len(got) == 1


def test_get_includes_the_version(tmp_path):
    import shutil
    from pathlib import Path
    from desktop.settings_ui import SettingsApi
    cfg = tmp_path / "config.toml"
    shutil.copy(Path(__file__).parent.parent / "config.toml", cfg)
    assert SettingsApi(cfg, version=lambda: "Version 0.1.4").get()["version"] == "Version 0.1.4"


def test_rejected_write_leaves_original_untouched(cfg_path, monkeypatch):
    before = cfg_path.read_text(encoding="utf-8")
    v = su.read_values(cfg_path) | {"min_pick_rate": 2.0}
    def boom(path=None, strict=False):
        raise ValueError("does not load")
    monkeypatch.setattr(su, "load_config", boom)
    with pytest.raises(ValueError):
        su.save(v, cfg_path)
    assert cfg_path.read_text(encoding="utf-8") == before
    assert not cfg_path.with_suffix(".toml.tmp").exists()

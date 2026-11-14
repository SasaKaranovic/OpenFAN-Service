import pytest

from openfan_service.config_store import ConfigError, ConfigStore, dump_config, parse_config_text
from openfan_service.models import Config, Curve, Fan, slugify


def test_points_are_sorted_numerically():
    # The old service sorted '10,..' before '5,..' as strings.
    c = Curve(id="a", name="A", points=[(5, 20), (10, 30), (100, 100), (45, 60)])
    assert [p[0] for p in c.points] == [5, 10, 45, 100]


@pytest.mark.parametrize("points", [[], [(20, 120)], [(20, 10), (20, 30)], [(300, 10)]])
def test_bad_points_rejected(points):
    with pytest.raises(ValueError):
        Curve(id="a", name="A", points=points)


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        Config(curves=[Curve(id="a", name="A", points=[(1, 2)]), Curve(id="a", name="B", points=[(1, 2)])])


def test_fan_may_point_at_deleted_curve():
    cfg = Config(fans=[Fan(controller="main", index=0, curve="gone")])
    assert cfg.curve("gone") is None


def test_slugify_unique():
    assert slugify("HDD cage") == "hdd-cage"
    assert slugify("HDD cage", {"hdd-cage"}) == "hdd-cage-2"
    assert slugify("!!!") == "item"


def test_new_store_creates_config(tmp_path):
    store = ConfigStore(tmp_path)
    assert (tmp_path / "config.yaml").exists()
    assert store.config.controllers[0].type == "openfan_serial"
    assert store.config.controllers[0].options["port"] == "auto"


def test_env_defaults(tmp_path):
    store = ConfigStore(tmp_path, controller_url="http://10.0.0.5:3000")
    assert store.config.controllers[0].type == "openfan_rest"


def test_save_is_atomic_and_backed_up(tmp_path):
    store = ConfigStore(tmp_path)
    store.mutate(lambda d: d["curves"].append({"id": "x", "name": "X", "points": [[30, 40]]}))
    store.mutate(lambda d: d["settings"].update(update_interval=3))
    assert len(store.list_backups()) == 2
    reloaded = ConfigStore(tmp_path)
    assert reloaded.config.curve("x").points == [(30.0, 40.0)]
    assert reloaded.config.settings.update_interval == 3
    assert not list(tmp_path.glob(".config-*.tmp"))


def test_invalid_change_is_not_saved(tmp_path):
    store = ConfigStore(tmp_path)
    before = (tmp_path / "config.yaml").read_text()
    with pytest.raises(ConfigError):
        store.mutate(lambda d: d["settings"].update(update_interval=0))
    assert (tmp_path / "config.yaml").read_text() == before


def test_broken_file_falls_back_to_backup(tmp_path):
    store = ConfigStore(tmp_path)
    store.mutate(lambda d: d["curves"].append({"id": "keep", "name": "Keep", "points": [[30, 40]]}))
    store.mutate(lambda d: d["settings"].update(update_interval=4))
    (tmp_path / "config.yaml").write_text("curves: [this is: not: valid")
    again = ConfigStore(tmp_path)
    assert again.load_warning and "backup" in again.load_warning
    assert again.config.curve("keep") is not None
    # the broken file is left for the user to inspect
    assert "not: valid" in (tmp_path / "config.yaml").read_text()


def test_broken_file_without_backup_uses_defaults(tmp_path):
    (tmp_path / "config.yaml").write_text(": : :")
    store = ConfigStore(tmp_path)
    assert store.load_warning
    assert store.config.controllers


def test_round_trip(tmp_path):
    cfg = Config(curves=[Curve(id="a", name="Ä curve", points=[(30, 40), (40.5, 60)], sensors=["s:x"])])
    assert parse_config_text(dump_config(cfg)) == cfg


def test_backup_prune(tmp_path):
    store = ConfigStore(tmp_path)
    store.mutate(lambda d: d["settings"].update(backups_to_keep=3))
    for i in range(6):
        store.mutate(lambda d, i=i: d["settings"].update(history_interval=10 + i))
    assert len(store.list_backups()) == 3


def test_read_backup_rejects_path_traversal(tmp_path):
    store = ConfigStore(tmp_path)
    with pytest.raises(ConfigError):
        store.read_backup("../config.yaml")

import datetime
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pscode-collab"))
import config  # noqa: E402
import reap  # noqa: E402
import state  # noqa: E402

NOW = "2026-08-21T00:00:00Z"


def _parse(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


class FakeContainer:
    def __init__(self):
        self.destroyed = []

    def destroy(self, name):
        self.destroyed.append(name)


def _deps(tmp_path, recs):
    store = state.Store(str(tmp_path / "s.json"))
    for rec in recs:
        store.put(rec)
    return types.SimpleNamespace(store=store, container=FakeContainer(),
                                 now=lambda: NOW, parse_time=_parse)


def _rec(name, revoked):
    return {"name": name, "port": 47100, "path": "/c/" + name,
            "gitea_token_id": 1, "created_at": NOW, "revoked_at": revoked}


def test_live_collaborator_is_never_reaped(tmp_path):
    d = _deps(tmp_path, [_rec("ada", None)])
    assert reap.sweep(d) == []
    assert d.container.destroyed == []


def test_recently_revoked_is_retained(tmp_path):
    d = _deps(tmp_path, [_rec("ada", "2026-08-20T00:00:00Z")])   # 1 day
    assert reap.sweep(d) == []


def test_expired_is_destroyed_and_dropped(tmp_path):
    old = "2026-08-01T00:00:00Z"                                  # 20 days
    d = _deps(tmp_path, [_rec("ada", old)])
    assert reap.sweep(d) == ["ada"]
    assert d.container.destroyed == ["ada"]
    assert d.store.get("ada") is None


def test_boundary_day_is_reaped_not_spared(tmp_path):
    exact = (_parse(NOW) - datetime.timedelta(days=config.RETENTION_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    d = _deps(tmp_path, [_rec("ada", exact)])
    assert reap.sweep(d) == ["ada"]


def test_sweep_is_bounded_per_run(tmp_path):
    old = "2026-08-01T00:00:00Z"
    d = _deps(tmp_path, [_rec("c%d" % i, old) for i in range(20)])
    assert len(reap.sweep(d)) <= 8


def test_mixed_batch_reaps_only_the_expired(tmp_path):
    d = _deps(tmp_path, [
        _rec("live", None),
        _rec("fresh", "2026-08-20T00:00:00Z"),
        _rec("expired", "2026-08-01T00:00:00Z"),
    ])
    assert reap.sweep(d) == ["expired"]
    assert d.store.get("live") is not None
    assert d.store.get("fresh") is not None
    assert d.store.get("expired") is None

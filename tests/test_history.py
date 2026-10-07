import datetime as dt
import history


def _rows(n=5):
    t0 = dt.datetime(2026, 10, 1, 8, 0, 0)
    return [("z1", t0 + dt.timedelta(minutes=5 * i), 10.0 + i) for i in range(n)]


def test_insert_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "t.db"))
    history.init_tables()
    history.insert_history(_rows(), "simulated")
    out = history.recent_history("z1", n=3)
    assert len(out) == 3
    assert out[-1][1] == 14.0


def test_duplicates_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "t.db"))
    history.init_tables()
    history.insert_history(_rows(), "simulated")
    history.insert_history(_rows(), "simulated")
    assert len(history.recent_history("z1", n=50)) == 5


def test_clear_source(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", str(tmp_path / "t.db"))
    history.init_tables()
    history.insert_history(_rows(), "simulated")
    history.clear_source("simulated")
    assert history.recent_history("z1") == []
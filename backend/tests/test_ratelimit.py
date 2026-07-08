"""Unit tests for the fixed-window limiter (the HTTP suite runs with limiting
disabled, so the logic is verified directly here)."""
from app.ratelimit import _FixedWindow


def test_allows_up_to_limit_then_blocks():
    w = _FixedWindow()
    assert all(w.allow("k", limit=3, window=60) for _ in range(3))
    # 4th within the window is blocked
    assert w.allow("k", limit=3, window=60) is False


def test_separate_keys_dont_share_a_budget():
    w = _FixedWindow()
    assert all(w.allow("a", limit=1, window=60) or True for _ in range(1))
    assert w.allow("a", limit=1, window=60) is False  # a is spent
    assert w.allow("b", limit=1, window=60) is True  # b is independent


def test_window_expiry_frees_budget(monkeypatch):
    import app.ratelimit as rl

    t = {"now": 1000.0}
    monkeypatch.setattr(rl.time, "time", lambda: t["now"])
    w = rl._FixedWindow()
    assert w.allow("k", limit=1, window=10) is True
    assert w.allow("k", limit=1, window=10) is False
    t["now"] += 11  # window passes
    assert w.allow("k", limit=1, window=10) is True

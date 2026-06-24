"""Unit tests for app.db.profiles.emails_for_user_ids (no Supabase)."""
from __future__ import annotations

from app.db import profiles


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def execute(self):
        return _Resp(self._data)


class _Client:
    def __init__(self, data):
        self._data = data

    def table(self, name):
        assert name == "profiles"
        return _Query(self._data)


def test_maps_ids_to_emails_and_drops_missing(monkeypatch):
    data = [
        {"id": "u1", "email": "alice@x.test"},
        {"id": "u2", "email": "bob@x.test"},
        {"id": "u3", "email": None},   # profile without an email → omitted
    ]
    monkeypatch.setattr(profiles, "require_client", lambda: _Client(data))
    out = profiles.emails_for_user_ids(["u1", "u2", "u3", "u1", None])
    assert out == {"u1": "alice@x.test", "u2": "bob@x.test"}


def test_empty_input_does_not_query(monkeypatch):
    def _boom():
        raise AssertionError("must not query the DB for empty input")

    monkeypatch.setattr(profiles, "require_client", _boom)
    assert profiles.emails_for_user_ids([]) == {}
    assert profiles.emails_for_user_ids([None, None]) == {}

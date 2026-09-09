"""Shared test isolation.

Every test gets its own auth config path. Without this the suite reads the
developer's real `config/auth.json`, so enabling auth on this machine breaks
tests that have nothing to do with auth -- and, worse, disabling it would make
the auth tests pass for the wrong reason.
"""
from __future__ import annotations

import pytest

from backend import auth


@pytest.fixture(autouse=True)
def isolate_auth_config(tmp_path, monkeypatch):
    """No config file means auth is disabled, which is what the non-auth tests
    expect. Tests that need auth on write their own file to this path."""
    monkeypatch.setattr(auth, "CONFIG_PATH", tmp_path / "auth.json")
    from backend.routes import auth as auth_route
    auth_route._FAILURES.clear()
    yield

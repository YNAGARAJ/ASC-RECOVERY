"""Tests for db.base.make_engine's F-06/F-07... F-07 (docs/audit/REGISTER.md)
defensive `sslmode` default. Pure -- `create_engine` never opens a
connection until first use, so this doesn't need a live Postgres and
isn't gated behind `TEST_DATABASE_URL` like the rest of tests/db/.
"""

from __future__ import annotations

from db.base import make_engine


def test_defaults_to_sslmode_require_when_absent() -> None:
    engine = make_engine("postgresql+psycopg://user:pass@example.com:5432/db")
    assert engine.url.query.get("sslmode") == "require"


def test_respects_an_explicit_sslmode_disable() -> None:
    engine = make_engine("postgresql+psycopg://user:pass@localhost:5432/db?sslmode=disable")
    assert engine.url.query.get("sslmode") == "disable"


def test_respects_an_explicit_sslmode_verify_full() -> None:
    engine = make_engine("postgresql+psycopg://user:pass@example.com:5432/db?sslmode=verify-full")
    assert engine.url.query.get("sslmode") == "verify-full"


def test_does_not_disturb_other_query_parameters() -> None:
    engine = make_engine("postgresql+psycopg://user:pass@example.com:5432/db?connect_timeout=5")
    assert engine.url.query.get("connect_timeout") == "5"
    assert engine.url.query.get("sslmode") == "require"

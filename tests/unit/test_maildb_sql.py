from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from maildb import maildb as maildb_module
from maildb.config import Settings
from maildb.maildb import MailDB


class QueryCapture:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[dict[str, Any] | None] = []

    def __call__(
        self,
        pool: object,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        return []


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class FakeTransaction:
    def __enter__(self) -> FakeTransaction:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def cursor(self, **kwargs: object) -> FakeCursor:
        return self.cursor_instance


class FakePool:
    def __init__(self) -> None:
        self.connection_instance = FakeConnection()

    def connection(self) -> FakeConnection:
        return self.connection_instance


def _db() -> MailDB:
    return MailDB._from_pool(
        object(),  # type: ignore[arg-type]
        config=Settings(user_email="me@example.com", _env_file=None),  # type: ignore[call-arg]
    )


def test_find_uses_deterministic_order_clauses(monkeypatch) -> None:
    capture = QueryCapture()
    monkeypatch.setattr(maildb_module, "_query_dicts", capture)
    db = _db()

    db.find()
    db.find(order="date ASC")
    db.find(order="sender_address DESC")

    assert "ORDER BY date DESC NULLS LAST, id LIMIT" in capture.sql[0]
    assert "ORDER BY date ASC NULLS FIRST, id LIMIT" in capture.sql[1]
    assert "ORDER BY sender_address DESC, id LIMIT" in capture.sql[2]


def test_find_default_sql_has_no_window_count(monkeypatch) -> None:
    capture = QueryCapture()
    monkeypatch.setattr(maildb_module, "_query_dicts", capture)
    db = _db()

    results, total = db.find()

    assert "COUNT(*) OVER()" not in capture.sql[0]
    assert "COUNT(*)" not in capture.sql[0]
    assert total is None
    assert results == []


def test_find_include_total_issues_count_query(monkeypatch) -> None:
    dicts_capture = QueryCapture()
    monkeypatch.setattr(maildb_module, "_query_dicts", dicts_capture)

    def fake_one(
        pool: object,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        dicts_capture.sql.append(" ".join(sql.split()))
        dicts_capture.params.append(params)
        return {"n": 7}

    monkeypatch.setattr(maildb_module, "_query_one_dict", fake_one)
    db = _db()

    results, total = db.find(include_total=True)

    assert "COUNT(*) OVER()" not in dicts_capture.sql[0]
    assert "SELECT COUNT(*) AS n FROM emails WHERE" in dicts_capture.sql[1]
    assert total == 7
    assert results == []


def test_correspondence_uses_deterministic_order_clause(monkeypatch) -> None:
    capture = QueryCapture()
    monkeypatch.setattr(maildb_module, "_query_dicts", capture)
    db = _db()

    db.correspondence(address="alice@example.com")

    assert "ORDER BY date ASC NULLS FIRST, id LIMIT" in capture.sql[0]


def test_literal_date_desc_queries_use_tiebreakers(monkeypatch) -> None:
    capture = QueryCapture()
    monkeypatch.setattr(maildb_module, "_query_dicts", capture)
    db = _db()

    db.mention_search(text="budget")
    db.unreplied(direction="inbound", account="me@example.com")
    db.unreplied(direction="outbound", account="me@example.com")

    assert "ORDER BY date DESC NULLS LAST, id LIMIT" in capture.sql[0]
    assert "ORDER BY e.date DESC NULLS LAST, e.id LIMIT" in capture.sql[1]
    assert "ORDER BY e.date DESC NULLS LAST, e.id LIMIT" in capture.sql[2]


def test_search_sets_hnsw_ef_search_before_vector_query() -> None:
    pool = FakePool()
    embedding_client = MagicMock()
    embedding_client.embed.return_value = [0.1] * 768
    db = MailDB._from_pool(
        pool,  # type: ignore[arg-type]
        config=Settings(_env_file=None),  # type: ignore[call-arg]
        embedding_client=embedding_client,
    )

    _, total = db.search("budget", limit=5, offset=41)

    executed = pool.connection_instance.cursor_instance.executed
    assert executed[0] == (
        "SELECT set_config('hnsw.ef_search', %(ef_search)s, true)",
        {"ef_search": "46"},
    )
    assert "COUNT(*) OVER()" not in executed[1][0]
    assert "ORDER BY embedding <=> %(query_embedding)s::vector" in executed[1][0]
    assert total == 41

# tests/unit/test_dsl.py
"""Unit tests for the DSL parser — no database required."""

from __future__ import annotations

import pytest

from maildb.dsl import build_where_clause, parse_query

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_rejects_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="Unknown source"):
            parse_query({"from": "nonexistent"})

    def test_rejects_unknown_column_in_where(self) -> None:
        with pytest.raises(ValueError, match="Unknown column"):
            parse_query({"where": {"field": "bad_col", "op": "eq", "value": 1}})

    def test_rejects_unknown_operator(self) -> None:
        with pytest.raises(ValueError, match="Unknown operator"):
            parse_query({"where": {"field": "subject", "op": "regex", "value": "x"}})

    def test_enforces_row_limit_cap(self) -> None:
        sql, _ = parse_query({"limit": 9999})
        assert "LIMIT 1000" in sql

    def test_rejects_body_text_in_grouped_select(self) -> None:
        with pytest.raises(ValueError, match="body_text cannot be selected"):
            parse_query(
                {
                    "select": [{"field": "body_text"}],
                    "group_by": ["sender_domain"],
                }
            )

    def test_default_source_is_emails(self) -> None:
        sql, _ = parse_query({})
        assert "FROM emails" in sql


# ---------------------------------------------------------------------------
# Where operators
# ---------------------------------------------------------------------------


class TestWhereOperators:
    def test_eq(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "sender_domain", "op": "eq", "value": "acme.com"},
            }
        )
        assert "sender_domain = %(__p0)s" in sql
        assert params["__p0"] == "acme.com"

    def test_neq(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "sender_domain", "op": "neq", "value": "acme.com"},
            }
        )
        assert "sender_domain != %(__p0)s" in sql
        assert params["__p0"] == "acme.com"

    def test_gt_gte_lt_lte(self) -> None:
        for op_name, sql_op in [("gt", ">"), ("gte", ">="), ("lt", "<"), ("lte", "<=")]:
            sql, params = parse_query(
                {
                    "where": {"field": "date", "op": op_name, "value": "2025-01-01"},
                }
            )
            assert f"date {sql_op} %(__p0)s" in sql
            assert params["__p0"] == "2025-01-01"

    def test_ilike(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "subject", "op": "ilike", "value": "%budget%"},
            }
        )
        assert "subject ILIKE %(__p0)s" in sql
        assert params["__p0"] == "%budget%"

    def test_in_list(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "sender_domain", "op": "in", "value": ["a.com", "b.com"]},
            }
        )
        assert "sender_domain IN %(__p0)s" in sql
        assert params["__p0"] == ("a.com", "b.com")

    def test_is_null_true(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "in_reply_to", "op": "is_null", "value": True},
            }
        )
        assert "in_reply_to IS NULL" in sql
        assert not params

    def test_is_null_false(self) -> None:
        sql, _params = parse_query(
            {
                "where": {"field": "in_reply_to", "op": "is_null", "value": False},
            }
        )
        assert "in_reply_to IS NOT NULL" in sql

    def test_contains_array(self) -> None:
        sql, params = parse_query(
            {
                "where": {"field": "labels", "op": "contains", "value": ["INBOX"]},
            }
        )
        assert "labels @> %(__p0)s" in sql
        assert params["__p0"] == ["INBOX"]


# ---------------------------------------------------------------------------
# Boolean combinators
# ---------------------------------------------------------------------------


class TestBooleanCombinators:
    def test_and(self) -> None:
        sql, params = parse_query(
            {
                "where": {
                    "and": [
                        {"field": "sender_domain", "op": "eq", "value": "a.com"},
                        {"field": "has_attachment", "op": "eq", "value": True},
                    ],
                },
            }
        )
        assert "AND" in sql
        assert len(params) == 2

    def test_or(self) -> None:
        sql, _params = parse_query(
            {
                "where": {
                    "or": [
                        {"field": "sender_domain", "op": "eq", "value": "a.com"},
                        {"field": "sender_domain", "op": "eq", "value": "b.com"},
                    ],
                },
            }
        )
        assert "OR" in sql

    def test_not(self) -> None:
        sql, _ = parse_query(
            {
                "where": {"not": {"field": "has_attachment", "op": "eq", "value": True}},
            }
        )
        assert "NOT" in sql

    def test_nested(self) -> None:
        sql, params = parse_query(
            {
                "where": {
                    "and": [
                        {
                            "or": [
                                {"field": "sender_domain", "op": "eq", "value": "a.com"},
                                {"field": "sender_domain", "op": "eq", "value": "b.com"},
                            ],
                        },
                        {"field": "has_attachment", "op": "eq", "value": True},
                    ],
                },
            }
        )
        assert "AND" in sql
        assert "OR" in sql
        assert len(params) == 3


# ---------------------------------------------------------------------------
# Select expressions
# ---------------------------------------------------------------------------


class TestSelect:
    def test_field_ref(self) -> None:
        sql, _ = parse_query({"select": [{"field": "subject"}]})
        assert "SELECT subject" in sql

    def test_field_with_alias(self) -> None:
        sql, _ = parse_query({"select": [{"field": "sender_domain", "as": "domain"}]})
        assert "sender_domain AS domain" in sql

    def test_count_star(self) -> None:
        sql, _ = parse_query(
            {
                "select": [{"count": "*", "as": "total"}],
                "group_by": ["sender_domain"],
            }
        )
        assert "count(*) AS total" in sql

    def test_count_distinct(self) -> None:
        sql, _ = parse_query(
            {
                "select": [{"count_distinct": "thread_id", "as": "threads"}],
                "group_by": ["sender_domain"],
            }
        )
        assert "count(DISTINCT thread_id) AS threads" in sql

    def test_min_max(self) -> None:
        sql, _ = parse_query(
            {
                "select": [
                    {"min": "date", "as": "earliest"},
                    {"max": "date", "as": "latest"},
                ],
                "group_by": ["sender_domain"],
            }
        )
        assert "min(date) AS earliest" in sql
        assert "max(date) AS latest" in sql

    def test_date_trunc(self) -> None:
        sql, _ = parse_query(
            {
                "select": [{"date_trunc": "month", "field": "date", "as": "period"}],
                "group_by": ["sender_domain"],
            }
        )
        assert "date_trunc('month', date) AS period" in sql


# ---------------------------------------------------------------------------
# GROUP BY / HAVING / ORDER BY
# ---------------------------------------------------------------------------


class TestGroupByHavingOrderBy:
    def test_group_by(self) -> None:
        sql, _ = parse_query(
            {
                "select": [{"field": "sender_domain"}, {"count": "*", "as": "cnt"}],
                "group_by": ["sender_domain"],
            }
        )
        assert "GROUP BY sender_domain" in sql

    def test_having(self) -> None:
        sql, params = parse_query(
            {
                "select": [{"field": "sender_domain"}, {"count": "*", "as": "cnt"}],
                "group_by": ["sender_domain"],
                "having": {"field": "cnt", "op": "gte", "value": 2},
            }
        )
        assert "HAVING count(*) >= %(__p0)s" in sql
        assert params["__p0"] == 2

    def test_order_by(self) -> None:
        sql, _ = parse_query(
            {
                "select": [{"field": "sender_domain"}, {"count": "*", "as": "cnt"}],
                "group_by": ["sender_domain"],
                "order_by": [{"field": "cnt", "dir": "DESC"}],
            }
        )
        assert "ORDER BY cnt DESC" in sql

    def test_group_by_alias(self) -> None:
        """group_by should accept select aliases and expand to underlying expression."""
        sql, _ = parse_query(
            {
                "select": [
                    {"date_trunc": "month", "field": "date", "as": "month"},
                    {"count": "*", "as": "n"},
                ],
                "group_by": ["month"],
                "order_by": [{"field": "month", "dir": "asc"}],
            }
        )
        assert "GROUP BY date_trunc('month', date)" in sql
        assert "ORDER BY month ASC" in sql

    def test_group_by_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown column in group_by"):
            parse_query(
                {
                    "select": [{"count": "*", "as": "n"}],
                    "group_by": ["nonexistent"],
                }
            )

    def test_default_order_is_date_desc(self) -> None:
        sql, _ = parse_query({})
        assert "ORDER BY date DESC" in sql


# ---------------------------------------------------------------------------
# Sources / CTEs
# ---------------------------------------------------------------------------


class TestSources:
    def test_sent_to_has_cte(self) -> None:
        sql, _ = parse_query({"from": "sent_to"})
        assert "WITH source AS" in sql
        assert "LATERAL" in sql
        assert "FROM source" in sql

    def test_email_labels_has_cte(self) -> None:
        sql, _ = parse_query({"from": "email_labels"})
        assert "WITH source AS" in sql
        assert "unnest" in sql
        assert "FROM source" in sql

    def test_sent_to_allows_recipient_columns(self) -> None:
        sql, _ = parse_query(
            {
                "from": "sent_to",
                "where": {"field": "recipient_domain", "op": "eq", "value": "x.com"},
            }
        )
        assert "recipient_domain = %(__p0)s" in sql

    def test_emails_rejects_recipient_columns(self) -> None:
        with pytest.raises(ValueError, match="Unknown column"):
            parse_query(
                {
                    "where": {"field": "recipient_domain", "op": "eq", "value": "x.com"},
                }
            )


# ---------------------------------------------------------------------------
# build_where_clause public API
# ---------------------------------------------------------------------------


class TestBuildWhereClause:
    def test_basic_filter(self) -> None:
        sql, params = build_where_clause({"field": "sender_domain", "op": "eq", "value": "a.com"})
        assert "sender_domain" in sql
        assert "a.com" in params.values()

    def test_rejects_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="Unknown source"):
            build_where_clause(
                {"field": "sender_domain", "op": "eq", "value": "a.com"}, source="bad"
            )

    def test_compound_filter(self) -> None:
        sql, params = build_where_clause(
            {
                "and": [
                    {"field": "sender_domain", "op": "eq", "value": "a.com"},
                    {"field": "date", "op": "gte", "value": "2025-01-01"},
                ]
            }
        )
        assert "AND" in sql
        assert len(params) == 2


# ---------------------------------------------------------------------------
# Security: alias & date_trunc validation
# ---------------------------------------------------------------------------


class TestSecurityValidation:
    def test_rejects_invalid_alias(self) -> None:
        with pytest.raises(ValueError, match="Invalid alias"):
            parse_query({"select": [{"field": "subject", "as": "bad alias!"}]})

    def test_rejects_sql_injection_alias(self) -> None:
        with pytest.raises(ValueError, match="Invalid alias"):
            parse_query({"select": [{"field": "subject", "as": "x; DROP TABLE emails"}]})

    def test_accepts_valid_alias(self) -> None:
        sql, _ = parse_query({"select": [{"field": "sender_domain", "as": "domain"}]})
        assert "AS domain" in sql

    def test_rejects_invalid_date_trunc_precision(self) -> None:
        with pytest.raises(ValueError, match="Invalid date_trunc precision"):
            parse_query(
                {
                    "select": [
                        {"date_trunc": "'; DROP TABLE emails; --", "field": "date", "as": "period"}
                    ],
                    "group_by": ["sender_domain"],
                }
            )

    def test_accepts_valid_date_trunc_precision(self) -> None:
        for precision in ("year", "month", "week", "day"):
            sql, _ = parse_query(
                {
                    "select": [{"date_trunc": precision, "field": "date", "as": "period"}],
                    "group_by": ["sender_domain"],
                }
            )
            assert f"date_trunc('{precision}', date)" in sql


def test_source_account_is_filterable():
    sql, params = parse_query(
        {
            "from": "emails",
            "select": [{"field": "id"}],
            "where": {"field": "source_account", "eq": "you@example.com"},
            "limit": 10,
        }
    )
    assert "source_account = %(__p0)s" in sql
    assert params["__p0"] == "you@example.com"


def test_import_id_is_filterable():
    sql, _params = parse_query(
        {
            "from": "emails",
            "select": [{"field": "id"}],
            "where": {"field": "import_id", "is_null": False},
            "limit": 10,
        }
    )
    assert "import_id IS NOT NULL" in sql

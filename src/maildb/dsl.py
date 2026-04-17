# src/maildb/dsl.py
"""JSON DSL -> parameterised SQL translator.

Translates a JSON query specification into a safe, parameterised SQL string
using strict column/operator whitelists and value binding.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Column whitelists
# ---------------------------------------------------------------------------

_EMAILS_COLUMNS: set[str] = {
    "id",
    "message_id",
    "thread_id",
    "subject",
    "sender_name",
    "sender_address",
    "sender_domain",
    "date",
    "body_text",
    "has_attachment",
    "labels",
    "in_reply_to",
    "created_at",
    "source_account",
    "import_id",
}

_SENT_TO_COLUMNS: set[str] = _EMAILS_COLUMNS | {
    "recipient_address",
    "recipient_domain",
    "recipient_type",
}

_EMAIL_LABELS_COLUMNS: set[str] = _EMAILS_COLUMNS | {"label"}

_SOURCE_COLUMNS: dict[str, set[str]] = {
    "emails": _EMAILS_COLUMNS,
    "sent_to": _SENT_TO_COLUMNS,
    "email_labels": _EMAIL_LABELS_COLUMNS,
}

# ---------------------------------------------------------------------------
# Operator whitelist
# ---------------------------------------------------------------------------

_OPERATORS: set[str] = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "ilike",
    "not_ilike",
    "in",
    "not_in",
    "contains",
    "is_null",
}

_OP_SQL: dict[str, str] = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "ilike": "ILIKE",
    "not_ilike": "NOT ILIKE",
}

# ---------------------------------------------------------------------------
# Aggregate whitelist
# ---------------------------------------------------------------------------

_AGGREGATES: set[str] = {
    "count",
    "count_distinct",
    "min",
    "max",
    "sum",
    "array_agg_distinct",
}

# ---------------------------------------------------------------------------
# Row-limit hard cap
# ---------------------------------------------------------------------------

_MAX_ROWS = 1000

# ---------------------------------------------------------------------------
# Date-trunc precision whitelist
# ---------------------------------------------------------------------------

_DATE_TRUNC_INTERVALS: set[str] = {"year", "month", "week", "day"}

# ---------------------------------------------------------------------------
# Alias validation
# ---------------------------------------------------------------------------

_ALIAS_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_alias(alias: str) -> None:
    if not _ALIAS_RE.match(alias):
        msg = f"Invalid alias '{alias}'. Must match [a-zA-Z_][a-zA-Z0-9_]*"
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# CTE templates
# ---------------------------------------------------------------------------

_SENT_TO_CTE = """\
WITH source AS (
    SELECT e.*, r.addr AS recipient_address,
           split_part(r.addr, '@', 2) AS recipient_domain, r.type AS recipient_type
    FROM emails e,
    LATERAL (
        SELECT jsonb_array_elements_text(e.recipients->'to') AS addr, 'to'::text AS type
        UNION ALL SELECT jsonb_array_elements_text(e.recipients->'cc'), 'cc'
        UNION ALL SELECT jsonb_array_elements_text(e.recipients->'bcc'), 'bcc'
    ) AS r
)"""

_EMAIL_LABELS_CTE = """\
WITH source AS (
    SELECT e.*, unnest(e.labels) AS label
    FROM emails e WHERE e.labels IS NOT NULL AND array_length(e.labels, 1) > 0
)"""

_SOURCE_CTE: dict[str, str] = {
    "sent_to": _SENT_TO_CTE,
    "email_labels": _EMAIL_LABELS_CTE,
}


# ---------------------------------------------------------------------------
# Internal: param-counter factory
# ---------------------------------------------------------------------------


class _ParamAccumulator:
    """Generates unique param names and accumulates values."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self._counter = 0

    def add(self, value: Any) -> str:
        name = f"__p{self._counter}"
        self._counter += 1
        self.params[name] = value
        return name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_query(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Parse a DSL spec dict into ``(sql_string, params_dict)``."""
    source, allowed = _resolve_source(spec)
    acc = _ParamAccumulator()
    has_group_by = bool(spec.get("group_by"))

    select_aliases: set[str] = set()
    alias_exprs: dict[str, str] = {}
    select_exprs = _resolve_select(
        spec.get("select"), allowed, has_group_by, select_aliases, alias_exprs
    )
    where_sql = _resolve_where(spec.get("where"), allowed, acc)
    group_sql = _resolve_group_by(spec.get("group_by"), allowed, select_aliases, alias_exprs)
    having_sql = _resolve_having(spec.get("having"), allowed, select_aliases, acc, alias_exprs)
    order_sql = _resolve_order_by(spec.get("order_by"), allowed, select_aliases, has_group_by)
    limit_sql = _resolve_limit(spec.get("limit", 50), spec.get("offset", 0))

    table = "source" if source != "emails" else "emails"
    select_str = ", ".join(select_exprs)
    body = (
        f"SELECT {select_str} FROM {table}{where_sql}{group_sql}{having_sql}{order_sql}{limit_sql}"
    )

    cte = _SOURCE_CTE.get(source)
    sql = f"{cte}\n{body}" if cte else body
    return sql, acc.params


def build_where_clause(
    where: dict[str, Any],
    source: str = "emails",
) -> tuple[str, dict[str, Any]]:
    """Build a WHERE clause from a DSL filter spec.

    Public API consumed by ``cluster()`` and other callers that need
    only the filter portion of a query.
    """
    if source not in _SOURCE_COLUMNS:
        msg = f"Unknown source: {source!r}"
        raise ValueError(msg)

    allowed = _SOURCE_COLUMNS[source]
    acc = _ParamAccumulator()
    fragment = _build_where(where, allowed, acc)
    return fragment, acc.params


# ---------------------------------------------------------------------------
# Clause builders (called from parse_query)
# ---------------------------------------------------------------------------


def _resolve_source(spec: dict[str, Any]) -> tuple[str, set[str]]:
    source: str = spec.get("from", "emails")
    if source not in _SOURCE_COLUMNS:
        msg = f"Unknown source: {source!r}"
        raise ValueError(msg)
    return source, _SOURCE_COLUMNS[source]


def _resolve_select(
    raw_select: list[dict[str, Any]] | None,
    allowed: set[str],
    has_group_by: bool,
    aliases: set[str],
    alias_exprs: dict[str, str] | None = None,
) -> list[str]:
    if raw_select:
        return _build_select(raw_select, allowed, has_group_by, aliases, alias_exprs)
    # Default: all columns with body_text truncated
    cols: list[str] = []
    for c in sorted(allowed):
        if c == "body_text":
            cols.append("left(body_text, 500) AS body_preview")
            aliases.add("body_preview")
        else:
            cols.append(c)
    return cols


def _resolve_where(
    where: dict[str, Any] | None,
    allowed: set[str],
    acc: _ParamAccumulator,
) -> str:
    if not where:
        return ""
    fragment = _build_where(where, allowed, acc)
    return f" WHERE {fragment}"


def _resolve_group_by(
    group_by: list[str] | None,
    allowed: set[str],
    select_aliases: set[str] | None = None,
    alias_exprs: dict[str, str] | None = None,
) -> str:
    if not group_by:
        return ""
    parts: list[str] = []
    for col in group_by:
        if col in allowed:
            parts.append(col)
        elif select_aliases and col in select_aliases:
            # Use the underlying expression for aliases (PostgreSQL requires
            # expressions, not aliases, in GROUP BY)
            if alias_exprs and col in alias_exprs:
                parts.append(alias_exprs[col])
            else:
                parts.append(col)
        else:
            msg = f"Unknown column in group_by: {col!r}"
            raise ValueError(msg)
    return f" GROUP BY {', '.join(parts)}"


def _resolve_having(
    having: dict[str, Any] | None,
    allowed: set[str],
    select_aliases: set[str],
    acc: _ParamAccumulator,
    alias_exprs: dict[str, str] | None = None,
) -> str:
    if not having:
        return ""
    having_allowed = allowed | select_aliases | set((alias_exprs or {}).values())
    # Rewrite alias references to underlying expressions for PostgreSQL compatibility
    having = _expand_having_aliases(having, alias_exprs or {})
    fragment = _build_where(having, having_allowed, acc)
    return f" HAVING {fragment}"


def _expand_having_aliases(clause: dict[str, Any], alias_exprs: dict[str, str]) -> dict[str, Any]:
    """Replace alias references in HAVING field names with their underlying SQL expressions."""
    if not alias_exprs:
        return clause
    # Boolean combinators
    for key in ("and", "or"):
        if key in clause:
            return {key: [_expand_having_aliases(sub, alias_exprs) for sub in clause[key]]}
    if "not" in clause:
        return {"not": _expand_having_aliases(clause["not"], alias_exprs)}
    # Leaf: substitute field if it's an alias
    if "field" in clause and clause["field"] in alias_exprs:
        clause = {**clause, "field": alias_exprs[clause["field"]]}
    return clause


def _resolve_order_by(
    order_by: list[dict[str, Any]] | None,
    allowed: set[str],
    select_aliases: set[str],
    has_group_by: bool,
) -> str:
    if not order_by:
        return "" if has_group_by else " ORDER BY date DESC"
    parts: list[str] = []
    for item in order_by:
        col = item["field"]
        direction = item.get("dir", "ASC").upper()
        if direction not in ("ASC", "DESC"):
            msg = f"Invalid order direction: {direction!r}"
            raise ValueError(msg)
        if col not in allowed and col not in select_aliases:
            msg = f"Unknown column in order_by: {col!r}"
            raise ValueError(msg)
        parts.append(f"{col} {direction}")
    return f" ORDER BY {', '.join(parts)}"


def _resolve_limit(limit: int, offset: int) -> str:
    capped = min(int(limit), _MAX_ROWS)
    sql = f" LIMIT {capped}"
    if offset:
        sql += f" OFFSET {int(offset)}"
    return sql


# ---------------------------------------------------------------------------
# Select / aggregate helpers
# ---------------------------------------------------------------------------


def _build_select(
    items: list[dict[str, Any]],
    allowed: set[str],
    has_group_by: bool,
    aliases: set[str],
    alias_exprs: dict[str, str] | None = None,
) -> list[str]:
    """Translate select items into SQL expression strings."""
    exprs: list[str] = []
    for item in items:
        expr = _select_item(item, allowed, has_group_by)
        exprs.append(expr)
        # Track alias
        if "as" in item:
            alias = item["as"]
            aliases.add(alias)
            # Track the expression behind the alias (strip " AS alias" suffix)
            if alias_exprs is not None:
                base_expr = expr[: expr.rfind(" AS ")]
                alias_exprs[alias] = base_expr
        elif (
            "field" in item
            and not any(k in item for k in _AGGREGATES)
            and "date_trunc" not in item
        ):
            aliases.add(item["field"])
    return exprs


def _select_item(
    item: dict[str, Any],
    allowed: set[str],
    has_group_by: bool,
) -> str:
    """Convert a single select item to a SQL expression."""
    if "as" in item:
        _validate_alias(item["as"])
        alias_suffix = f" AS {item['as']}"
    else:
        alias_suffix = ""

    # Date truncation
    if "date_trunc" in item:
        field = item["field"]
        if field not in allowed:
            msg = f"Unknown column in select: {field!r}"
            raise ValueError(msg)
        precision = item["date_trunc"]
        if precision not in _DATE_TRUNC_INTERVALS:
            msg = f"Invalid date_trunc precision: {precision!r}. Must be one of {sorted(_DATE_TRUNC_INTERVALS)}"
            raise ValueError(msg)
        return f"date_trunc('{precision}', {field}){alias_suffix}"

    # Aggregation
    for agg in _AGGREGATES:
        if agg in item:
            return _agg_expr(agg, item[agg], allowed, alias_suffix)

    # Plain field ref
    field = item["field"]
    if field not in allowed:
        msg = f"Unknown column in select: {field!r}"
        raise ValueError(msg)
    if has_group_by and field == "body_text":
        msg = "body_text cannot be selected in grouped queries"
        raise ValueError(msg)
    return f"{field}{alias_suffix}"


def _agg_expr(
    agg: str,
    field: str | None,
    allowed: set[str],
    alias_suffix: str,
) -> str:
    """Build an aggregate expression."""
    if agg == "count" and field == "*":
        return f"count(*){alias_suffix}"

    if field is not None and field != "*" and field not in allowed:
        msg = f"Unknown column in aggregate: {field!r}"
        raise ValueError(msg)

    match agg:
        case "count":
            return f"count({field}){alias_suffix}"
        case "count_distinct":
            return f"count(DISTINCT {field}){alias_suffix}"
        case "min":
            return f"min({field}){alias_suffix}"
        case "max":
            return f"max({field}){alias_suffix}"
        case "sum":
            return f"sum({field}){alias_suffix}"
        case "array_agg_distinct":
            return f"array_agg(DISTINCT {field}){alias_suffix}"
        case _:  # pragma: no cover
            msg = f"Unknown aggregate: {agg!r}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Where-clause builder (recursive)
# ---------------------------------------------------------------------------


def _build_where(
    where: dict[str, Any],
    allowed: set[str],
    acc: _ParamAccumulator,
) -> str:
    """Recursively build a WHERE/HAVING clause fragment.

    Returns an SQL fragment string.  Parameters are accumulated
    via the *acc* accumulator.
    """
    # Boolean combinators
    if "and" in where:
        parts = [_build_where(sub, allowed, acc) for sub in where["and"]]
        return f"({' AND '.join(parts)})"

    if "or" in where:
        parts = [_build_where(sub, allowed, acc) for sub in where["or"]]
        return f"({' OR '.join(parts)})"

    if "not" in where:
        inner = _build_where(where["not"], allowed, acc)
        return f"(NOT {inner})"

    # Leaf condition: {"field": "col", "op": "eq", "value": ...}
    # Also accept shorthand: {"field": "col", "eq": "val"} where operator is a key.
    field = where["field"]
    if "op" in where:
        op = where["op"]
        value = where.get("value")
    else:
        # Shorthand: find the operator key
        op_keys = [k for k in where if k in _OPERATORS]
        if not op_keys:
            msg = "Missing 'op' or operator key in where clause"
            raise ValueError(msg)
        op = op_keys[0]
        value = where[op]

    if field not in allowed:
        msg = f"Unknown column: {field!r}"
        raise ValueError(msg)
    if op not in _OPERATORS:
        msg = f"Unknown operator: {op!r}"
        raise ValueError(msg)

    # is_null -- no parameter needed
    if op == "is_null":
        if value:
            return f"{field} IS NULL"
        return f"{field} IS NOT NULL"

    # in / not_in
    if op in ("in", "not_in"):
        pname = acc.add(tuple(value))  # type: ignore[arg-type]
        keyword = "IN" if op == "in" else "NOT IN"
        return f"{field} {keyword} %({pname})s"

    # contains (array containment)
    if op == "contains":
        pname = acc.add(value)
        return f"{field} @> %({pname})s"

    # Standard comparison / pattern operators
    sql_op = _OP_SQL[op]
    pname = acc.add(value)
    return f"{field} {sql_op} %({pname})s"

# src/chronicle_server/chronicle.py
"""Chronicle time-bucket aggregates: lanes, density navigator, extent.

Null-date records are excluded from all timeline aggregates (``date IS NOT NULL``);
they remain reachable via list endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chronicle_server.auth import require_user
from chronicle_server.scope import QueryScope, scope_filters, scope_fingerprint

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["chronicle"])

VALID_UNITS: tuple[str, ...] = ("hour", "day", "week", "month", "quarter", "year")
VALID_LANES = frozenset({"messages", "attachments", "people"})
MAX_BUCKETS = 2000
MIN_PIXEL_WIDTH = 320
MAX_PIXEL_WIDTH = 8192
DENSITY_PIXEL_WIDTH = 1600

# Approximate unit widths for bucket-count estimation (pixel-width rule).
_UNIT_SECONDS: dict[str, float] = {
    "hour": 3600.0,
    "day": 86400.0,
    "week": 7 * 86400.0,
    "month": 30 * 86400.0,
    "quarter": 91 * 86400.0,
    "year": 365 * 86400.0,
}


class AggregationTooFineError(ValueError):
    """Explicit aggregation would exceed the 2000-bucket ceiling."""

    def __init__(self, requested: str, smallest_valid_unit: str) -> None:
        self.requested = requested
        self.smallest_valid_unit = smallest_valid_unit
        super().__init__(
            f"aggregation {requested!r} exceeds {MAX_BUCKETS} buckets; "
            f"smallest valid unit is {smallest_valid_unit!r}"
        )


# --- request / response models ---


class TimeRange(BaseModel):
    from_: str = Field(..., alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class ExtentRange(BaseModel):
    from_: str | None = Field(None, alias="from")
    to: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class BucketPoint(BaseModel):
    bucket: str
    count: int


class DensitySeries(BaseModel):
    unit: str
    buckets: list[BucketPoint]


class BucketsRequest(BaseModel):
    scope: QueryScope = Field(default_factory=QueryScope)
    viewport: TimeRange
    pixel_width: int = 920
    aggregation: str = "auto"
    lanes: list[str] = Field(default_factory=lambda: ["messages", "attachments", "people"])

    @field_validator("aggregation")
    @classmethod
    def _check_aggregation(cls, value: str) -> str:
        allowed = {"auto", *VALID_UNITS}
        if value not in allowed:
            raise ValueError(f"aggregation must be one of {sorted(allowed)}; got {value!r}")
        return value

    @field_validator("lanes")
    @classmethod
    def _check_lanes(cls, value: list[str]) -> list[str]:
        unknown = [lane for lane in value if lane not in VALID_LANES]
        if unknown:
            raise ValueError(f"lanes must be subset of {sorted(VALID_LANES)}; unknown: {unknown}")
        return value


class BucketsResponse(BaseModel):
    scope_fingerprint: str
    aggregation: str
    unit: str
    viewport: TimeRange
    lanes: dict[str, list[BucketPoint]]
    density: DensitySeries
    extent: ExtentRange
    generated_at: str

    model_config = ConfigDict(populate_by_name=True)


# --- pure helpers ---


def clamp_pixel_width(pixel_width: int) -> int:
    """Clamp pixel width to the allowed server range [320, 8192]."""
    return max(MIN_PIXEL_WIDTH, min(MAX_PIXEL_WIDTH, pixel_width))


def estimated_bucket_count(duration: timedelta, unit: str) -> float:
    """Approximate number of *unit* buckets spanning *duration*."""
    seconds = max(duration.total_seconds(), 0.0)
    return seconds / _UNIT_SECONDS[unit]


def choose_aggregation(viewport_duration: timedelta, pixel_width: int) -> str:
    """Pick aggregation unit from viewport duration and pixel width.

    Target bucket width ≈ 8 px: ``target_buckets = pixel_width / 8``.
    Choose the smallest (finest) unit from
    ``[hour, day, week, month, quarter, year]`` whose estimated bucket count
    is ≤ ``max(target_buckets, 32)``. If even ``year`` exceeds that (or the
    2000-bucket soft ceiling), still return ``year``.
    """
    target_buckets = pixel_width / 8.0
    max_allowed = max(target_buckets, 32.0)
    for unit in VALID_UNITS:
        if estimated_bucket_count(viewport_duration, unit) <= max_allowed:
            return unit
    return "year"


def resolve_aggregation(
    aggregation: str,
    viewport_duration: timedelta,
    pixel_width: int,
) -> str:
    """Resolve ``auto`` or validate an explicit unit against the 2000 ceiling.

    Raises :class:`AggregationTooFineError` when an explicit unit would produce
    more than 2000 buckets (naming the finest unit that fits).
    """
    if aggregation == "auto":
        return choose_aggregation(viewport_duration, pixel_width)

    if estimated_bucket_count(viewport_duration, aggregation) <= MAX_BUCKETS:
        return aggregation

    smallest_valid = "year"
    for unit in VALID_UNITS:
        if estimated_bucket_count(viewport_duration, unit) <= MAX_BUCKETS:
            smallest_valid = unit
            break
    raise AggregationTooFineError(aggregation, smallest_valid)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO date or datetime string into an aware UTC datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Date-only: treat as midnight UTC.
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = text + "T00:00:00+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt: datetime = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return dt.isoformat().replace("+00:00", "Z")
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _and_clause(conditions: list[str]) -> str:
    if not conditions:
        return ""
    return " AND " + " AND ".join(conditions)


# --- SQL lane queries ---


def _fetch_bucket_rows(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any],
) -> list[BucketPoint]:
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    points: list[BucketPoint] = []
    for row in rows:
        bucket = _iso_utc(row[0])
        if bucket is None:
            continue
        points.append(BucketPoint(bucket=bucket, count=int(row[1])))
    return points


def _messages_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport_from: str,
    viewport_to: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> list[BucketPoint]:
    where = [
        "date IS NOT NULL",
        "date >= %(viewport_from)s",
        "date < %(viewport_to)s",
        *scope_conds,
    ]
    sql = f"""
        SELECT date_trunc(%(unit)s, date) AS bucket, count(*)::int AS count
        FROM emails
        WHERE {" AND ".join(where)}
        GROUP BY 1
        ORDER BY 1
    """
    params = {
        **scope_params,
        "unit": unit,
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
    }
    return _fetch_bucket_rows(pool, sql, params)


def _attachments_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport_from: str,
    viewport_to: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> list[BucketPoint]:
    # Scope filters target emails columns; unqualified names are unambiguous
    # because email_attachments has no date/source_account/sender_address.
    where = [
        "date IS NOT NULL",
        "date >= %(viewport_from)s",
        "date < %(viewport_to)s",
        *scope_conds,
    ]
    sql = f"""
        SELECT date_trunc(%(unit)s, e.date) AS bucket,
               count(ea.attachment_id)::int AS count
        FROM emails e
        JOIN email_attachments ea ON ea.email_id = e.id
        WHERE {" AND ".join(where)}
        GROUP BY 1
        ORDER BY 1
    """
    params = {
        **scope_params,
        "unit": unit,
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
    }
    return _fetch_bucket_rows(pool, sql, params)


def _people_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport_from: str,
    viewport_to: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> list[BucketPoint]:
    where = [
        "date IS NOT NULL",
        "date >= %(viewport_from)s",
        "date < %(viewport_to)s",
        *scope_conds,
    ]
    sql = f"""
        SELECT date_trunc(%(unit)s, date) AS bucket,
               count(DISTINCT sender_address)::int AS count
        FROM emails
        WHERE {" AND ".join(where)}
        GROUP BY 1
        ORDER BY 1
    """
    params = {
        **scope_params,
        "unit": unit,
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
    }
    return _fetch_bucket_rows(pool, sql, params)


def _extent(
    pool: ConnectionPool,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    sql = f"""
        SELECT min(date), max(date)
        FROM emails
        WHERE date IS NOT NULL{_and_clause(scope_conds)}
    """
    with pool.connection() as conn:
        row = conn.execute(sql, scope_params).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def _density_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> list[BucketPoint]:
    sql = f"""
        SELECT date_trunc(%(unit)s, date) AS bucket, count(*)::int AS count
        FROM emails
        WHERE date IS NOT NULL{_and_clause(scope_conds)}
        GROUP BY 1
        ORDER BY 1
    """
    params = {**scope_params, "unit": unit}
    return _fetch_bucket_rows(pool, sql, params)


_LANE_HANDLERS = {
    "messages": _messages_buckets,
    "attachments": _attachments_buckets,
    "people": _people_buckets,
}


def get_buckets(pool: ConnectionPool, body: BucketsRequest) -> BucketsResponse:
    """Compute lane aggregates, density series, and scope extent (read-only)."""
    pixel_width = clamp_pixel_width(body.pixel_width)
    vp_from = parse_iso_datetime(body.viewport.from_)
    vp_to = parse_iso_datetime(body.viewport.to)
    if vp_to <= vp_from:
        raise HTTPException(
            status_code=422,
            detail="viewport.to must be after viewport.from",
        )
    duration = vp_to - vp_from

    try:
        unit = resolve_aggregation(body.aggregation, duration, pixel_width)
    except AggregationTooFineError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "aggregation_exceeds_max_buckets",
                "max_buckets": MAX_BUCKETS,
                "requested": exc.requested,
                "smallest_valid_unit": exc.smallest_valid_unit,
            },
        ) from exc

    scope_conds, scope_params = scope_filters(body.scope)
    # Qualify scope column refs for the attachments join path that aliases emails.
    # Unqualified names work (email_attachments has no colliding columns), so we
    # pass the same conditions to every lane.

    viewport_from_s = body.viewport.from_
    viewport_to_s = body.viewport.to

    lane_data: dict[str, list[BucketPoint]] = {}
    for lane in body.lanes:
        handler = _LANE_HANDLERS[lane]
        lane_data[lane] = handler(
            pool,
            unit=unit,
            viewport_from=viewport_from_s,
            viewport_to=viewport_to_s,
            scope_conds=scope_conds,
            scope_params=scope_params,
        )

    extent_min, extent_max = _extent(pool, scope_conds, scope_params)
    if extent_min is not None and extent_max is not None:
        # Inclusive max date: use a 1-second span at least so duration > 0.
        extent_duration = max(extent_max - extent_min, timedelta(seconds=1))
        density_unit = choose_aggregation(extent_duration, DENSITY_PIXEL_WIDTH)
    else:
        density_unit = "year"

    density_points = _density_buckets(
        pool,
        unit=density_unit,
        scope_conds=scope_conds,
        scope_params=scope_params,
    )

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    fingerprint = scope_fingerprint(body.scope)

    logger.info(
        "chronicle_buckets",
        unit=unit,
        lanes=list(body.lanes),
        pixel_width=pixel_width,
        scope_fingerprint=fingerprint,
    )

    return BucketsResponse(
        scope_fingerprint=fingerprint,
        aggregation=unit,
        unit=unit,
        viewport=body.viewport,
        lanes=lane_data,
        density=DensitySeries(unit=density_unit, buckets=density_points),
        extent=ExtentRange(
            **{
                "from": _iso_utc(extent_min),
                "to": _iso_utc(extent_max),
            }
        ),
        generated_at=generated_at,
    )


@router.post("/buckets", response_model=BucketsResponse)
def chronicle_buckets(
    body: BucketsRequest,
    request: Request,
    _user: str = Depends(require_user),
) -> BucketsResponse:
    """Time-bucketed lane aggregates for a working-set scope and viewport."""
    pool: ConnectionPool = request.app.state.pool
    return get_buckets(pool, body)

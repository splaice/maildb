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
VALID_LANES = frozenset({"messages", "attachments", "people", "top_people", "events", "topics"})
MAX_BUCKETS = 2000
MIN_PIXEL_WIDTH = 320
MAX_PIXEL_WIDTH = 8192
DENSITY_PIXEL_WIDTH = 1600
TOP_PEOPLE_LIMIT = 8
TOP_TOPICS_LIMIT = 6
EVENTS_LANE_CAP = 500

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


class TopPeopleContactSeries(BaseModel):
    """Activity bucket series for one contact (activity spans only)."""

    contact_id: str
    display_name: str
    buckets: list[BucketPoint]


class TopPeopleLaneData(BaseModel):
    """top_people lane payload: top contacts by volume within viewport+scope."""

    contacts: list[TopPeopleContactSeries]


class TopicSeries(BaseModel):
    """Activity bucket series for one topic (member emails per bucket)."""

    topic_id: str
    label: str
    origin: str
    buckets: list[BucketPoint]


class TopicsLaneData(BaseModel):
    """topics lane: top visible topics by member volume within viewport+scope."""

    topics: list[TopicSeries]


class EventLaneMark(BaseModel):
    """Sparse event diamond mark for the events lane (not a bucket count)."""

    event_id: str
    title: str
    time_start: str
    time_end: str | None = None
    time_precision: str
    origin: str
    event_type: str
    status: str
    evidence_strength: str | None = None


class EventsLaneData(BaseModel):
    """events lane: individual marks (sparse diamonds), capped per viewport."""

    events: list[EventLaneMark]
    truncated: bool = False


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


LanePayload = list[BucketPoint] | TopPeopleLaneData | EventsLaneData | TopicsLaneData


class BucketsResponse(BaseModel):
    scope_fingerprint: str
    aggregation: str
    unit: str
    viewport: TimeRange
    lanes: dict[str, LanePayload]
    density: DensitySeries
    extent: ExtentRange
    generated_at: str

    model_config = ConfigDict(populate_by_name=True)


class CompareRequest(BaseModel):
    """Two-range comparison request (spec §4.7 Table 16)."""

    scope: QueryScope = Field(default_factory=QueryScope)
    a: TimeRange
    b: TimeRange
    pixel_width: int = 920
    lanes: list[str] = Field(default_factory=lambda: ["messages", "attachments", "people"])

    @field_validator("lanes")
    @classmethod
    def _check_lanes(cls, value: list[str]) -> list[str]:
        unknown = [lane for lane in value if lane not in VALID_LANES]
        if unknown:
            raise ValueError(f"lanes must be subset of {sorted(VALID_LANES)}; unknown: {unknown}")
        return value


class CompareSide(BaseModel):
    viewport: TimeRange
    lanes: dict[str, LanePayload]

    model_config = ConfigDict(populate_by_name=True)


class CompareTotalsSide(BaseModel):
    messages: int
    attachments: int


class CompareTotals(BaseModel):
    a: CompareTotalsSide
    b: CompareTotalsSide


class CompareResponse(BaseModel):
    unit: str
    aligned: bool
    a: CompareSide
    b: CompareSide
    totals: CompareTotals
    scope_fingerprint: str

    model_config = ConfigDict(populate_by_name=True)


# Align durations when relative difference is within this fraction.
ALIGNED_DURATION_TOLERANCE = 0.05


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


def _top_people_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport_from: str,
    viewport_to: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> TopPeopleLaneData:
    """Top contacts by sent-message volume within viewport+scope (activity spans).

    One set-based statement: emails ⨝ contact_addresses, ranked by volume,
    limited to top 8, excluding contacts whose every address is the user.
    Display name falls back to the address with the highest volume.
    """
    # Scope filters target emails columns (date/source_account/sender_address).
    # contact_addresses has none of those, so bare names stay unambiguous with
    # the emails alias — same pattern as the attachments lane.
    email_where = [
        "date IS NOT NULL",
        "date >= %(viewport_from)s",
        "date < %(viewport_to)s",
        *scope_conds,
    ]
    sql = f"""
        WITH filtered AS (
            SELECT ca.contact_id, e.date, e.sender_address
            FROM emails e
            JOIN contact_addresses ca ON ca.address = e.sender_address
            WHERE {" AND ".join(email_where)}
        ),
        eligible AS (
            SELECT ca.contact_id
            FROM contact_addresses ca
            GROUP BY ca.contact_id
            HAVING NOT bool_and(ca.is_user)
        ),
        ranked AS (
            SELECT f.contact_id, count(*)::int AS total
            FROM filtered f
            JOIN eligible el ON el.contact_id = f.contact_id
            GROUP BY f.contact_id
            ORDER BY total DESC, f.contact_id
            LIMIT %(top_limit)s
        ),
        bucketed AS (
            SELECT
                f.contact_id,
                date_trunc(%(unit)s, f.date) AS bucket,
                count(*)::int AS count
            FROM filtered f
            JOIN ranked r ON r.contact_id = f.contact_id
            GROUP BY f.contact_id, 2
        ),
        top_addr AS (
            SELECT contact_id, sender_address
            FROM (
                SELECT
                    f.contact_id,
                    f.sender_address,
                    count(*) AS vol,
                    row_number() OVER (
                        PARTITION BY f.contact_id
                        ORDER BY count(*) DESC, f.sender_address
                    ) AS rn
                FROM filtered f
                JOIN ranked r ON r.contact_id = f.contact_id
                GROUP BY f.contact_id, f.sender_address
            ) x
            WHERE rn = 1
        )
        SELECT
            r.contact_id::text,
            coalesce(nullif(c.display_name, ''), ta.sender_address) AS display_name,
            b.bucket,
            coalesce(b.count, 0)::int AS count,
            r.total
        FROM ranked r
        JOIN contacts c ON c.id = r.contact_id
        JOIN top_addr ta ON ta.contact_id = r.contact_id
        LEFT JOIN bucketed b ON b.contact_id = r.contact_id
        ORDER BY r.total DESC, r.contact_id, b.bucket
    """
    params = {
        **scope_params,
        "unit": unit,
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
        "top_limit": TOP_PEOPLE_LIMIT,
    }
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    # Group rows into contact series preserving rank order.
    by_contact: dict[str, TopPeopleContactSeries] = {}
    order: list[str] = []
    for row in rows:
        contact_id = str(row[0])
        display_name = str(row[1]) if row[1] is not None else contact_id
        if contact_id not in by_contact:
            by_contact[contact_id] = TopPeopleContactSeries(
                contact_id=contact_id,
                display_name=display_name,
                buckets=[],
            )
            order.append(contact_id)
        bucket = _iso_utc(row[2])
        if bucket is None:
            continue
        by_contact[contact_id].buckets.append(
            BucketPoint(bucket=bucket, count=int(row[3])),
        )
    return TopPeopleLaneData(contacts=[by_contact[cid] for cid in order])


def _topics_buckets(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport_from: str,
    viewport_to: str,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> TopicsLaneData:
    """Top 6 visible topics by member volume within viewport+scope.

    One set-based statement: members ⨝ emails ⨝ topics, ranked CTE like
    top_people. Hidden topics excluded. Bare emails columns are unambiguous
    (app_topic_members / app_topics do not share those names).
    """
    email_where = [
        "date IS NOT NULL",
        "date >= %(viewport_from)s",
        "date < %(viewport_to)s",
        *scope_conds,
    ]
    sql = f"""
        WITH filtered AS (
            SELECT m.topic_id, e.date
            FROM app_topic_members m
            JOIN emails e ON e.id = m.email_id
            JOIN app_topics t ON t.id = m.topic_id
            WHERE t.hidden = FALSE
              AND {" AND ".join(email_where)}
        ),
        ranked AS (
            SELECT topic_id, count(*)::int AS total
            FROM filtered
            GROUP BY topic_id
            ORDER BY total DESC, topic_id
            LIMIT %(top_limit)s
        ),
        bucketed AS (
            SELECT
                f.topic_id,
                date_trunc(%(unit)s, f.date) AS bucket,
                count(*)::int AS count
            FROM filtered f
            JOIN ranked r ON r.topic_id = f.topic_id
            GROUP BY f.topic_id, 2
        )
        SELECT
            r.topic_id::text,
            t.label,
            t.origin,
            b.bucket,
            coalesce(b.count, 0)::int AS count,
            r.total
        FROM ranked r
        JOIN app_topics t ON t.id = r.topic_id
        LEFT JOIN bucketed b ON b.topic_id = r.topic_id
        ORDER BY r.total DESC, r.topic_id, b.bucket
    """
    params = {
        **scope_params,
        "unit": unit,
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
        "top_limit": TOP_TOPICS_LIMIT,
    }
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    by_topic: dict[str, TopicSeries] = {}
    order: list[str] = []
    for row in rows:
        topic_id = str(row[0])
        label = str(row[1]) if row[1] is not None else topic_id
        origin = str(row[2])
        if topic_id not in by_topic:
            by_topic[topic_id] = TopicSeries(
                topic_id=topic_id,
                label=label,
                origin=origin,
                buckets=[],
            )
            order.append(topic_id)
        bucket = _iso_utc(row[3])
        if bucket is None:
            continue
        by_topic[topic_id].buckets.append(
            BucketPoint(bucket=bucket, count=int(row[4])),
        )
    return TopicsLaneData(topics=[by_topic[tid] for tid in order])


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


def _events_lane(
    pool: ConnectionPool,
    *,
    viewport_from: str,
    viewport_to: str,
    scope: QueryScope,
) -> EventsLaneData:
    """Individual events for the viewport (NOT bucket counts); dismissed excluded.

    Set-based single query. Cap EVENTS_LANE_CAP with truncated flag.
    Scope date only when present (person/topic scoping arrives later).
    Intersection: time_start < to AND coalesce(time_end, time_start) >= from.
    """
    conditions = [
        "time_start < %(viewport_to)s",
        "coalesce(time_end, time_start) >= %(viewport_from)s",
        "status <> 'dismissed'",
    ]
    params: dict[str, Any] = {
        "viewport_from": viewport_from,
        "viewport_to": viewport_to,
        "cap": EVENTS_LANE_CAP + 1,
    }
    if scope.date is not None:
        if scope.date.from_ is not None:
            conditions.append("time_start >= %(scope_from)s")
            params["scope_from"] = scope.date.from_
        if scope.date.to is not None:
            conditions.append("time_start < %(scope_to)s")
            params["scope_to"] = scope.date.to

    sql = f"""
        SELECT id, title, time_start, time_end, time_precision, origin,
               event_type, status, evidence_strength
          FROM app_events
         WHERE {" AND ".join(conditions)}
         ORDER BY time_start ASC, id ASC
         LIMIT %(cap)s
    """
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    truncated = len(rows) > EVENTS_LANE_CAP
    marks: list[EventLaneMark] = []
    for row in rows[:EVENTS_LANE_CAP]:
        marks.append(
            EventLaneMark(
                event_id=str(row[0]),
                title=str(row[1]),
                time_start=_iso_utc(row[2]) or "",
                time_end=_iso_utc(row[3]),
                time_precision=str(row[4]),
                origin=str(row[5]),
                event_type=str(row[6]),
                status=str(row[7]),
                evidence_strength=str(row[8]) if row[8] is not None else None,
            )
        )
    return EventsLaneData(events=marks, truncated=truncated)


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

    lane_data: dict[str, LanePayload] = {}
    for lane in body.lanes:
        if lane == "top_people":
            lane_data[lane] = _top_people_buckets(
                pool,
                unit=unit,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope_conds=scope_conds,
                scope_params=scope_params,
            )
        elif lane == "topics":
            lane_data[lane] = _topics_buckets(
                pool,
                unit=unit,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope_conds=scope_conds,
                scope_params=scope_params,
            )
        elif lane == "events":
            lane_data[lane] = _events_lane(
                pool,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope=body.scope,
            )
        else:
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


def durations_aligned(dur_a: timedelta, dur_b: timedelta) -> bool:
    """True when the two durations differ by at most 5% of the longer one."""
    a = abs(dur_a.total_seconds())
    b = abs(dur_b.total_seconds())
    longer = max(a, b)
    if longer <= 0:
        return True
    return abs(a - b) / longer <= ALIGNED_DURATION_TOLERANCE


def _lane_total(points: list[BucketPoint]) -> int:
    return sum(p.count for p in points)


def _fetch_lanes_for_viewport(
    pool: ConnectionPool,
    *,
    unit: str,
    viewport: TimeRange,
    lanes: list[str],
    scope: QueryScope,
    scope_conds: list[str],
    scope_params: dict[str, Any],
) -> dict[str, LanePayload]:
    """Run existing per-lane helpers for one viewport (no duplicated SQL)."""
    viewport_from_s = viewport.from_
    viewport_to_s = viewport.to
    lane_data: dict[str, LanePayload] = {}
    for lane in lanes:
        if lane == "top_people":
            lane_data[lane] = _top_people_buckets(
                pool,
                unit=unit,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope_conds=scope_conds,
                scope_params=scope_params,
            )
        elif lane == "topics":
            lane_data[lane] = _topics_buckets(
                pool,
                unit=unit,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope_conds=scope_conds,
                scope_params=scope_params,
            )
        elif lane == "events":
            lane_data[lane] = _events_lane(
                pool,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope=scope,
            )
        else:
            handler = _LANE_HANDLERS[lane]
            lane_data[lane] = handler(
                pool,
                unit=unit,
                viewport_from=viewport_from_s,
                viewport_to=viewport_to_s,
                scope_conds=scope_conds,
                scope_params=scope_params,
            )
    return lane_data


def _validate_range_bucket_ceiling(duration: timedelta, unit: str) -> None:
    """Raise 422 when *unit* would exceed the per-range bucket ceiling."""
    if estimated_bucket_count(duration, unit) <= MAX_BUCKETS:
        return
    smallest_valid = "year"
    for candidate in VALID_UNITS:
        if estimated_bucket_count(duration, candidate) <= MAX_BUCKETS:
            smallest_valid = candidate
            break
    raise HTTPException(
        status_code=422,
        detail={
            "error": "aggregation_exceeds_max_buckets",
            "max_buckets": MAX_BUCKETS,
            "requested": unit,
            "smallest_valid_unit": smallest_valid,
        },
    )


def get_compare(pool: ConnectionPool, body: CompareRequest) -> CompareResponse:
    """Compare two date ranges with a shared aggregation unit."""
    pixel_width = clamp_pixel_width(body.pixel_width)
    a_from = parse_iso_datetime(body.a.from_)
    a_to = parse_iso_datetime(body.a.to)
    b_from = parse_iso_datetime(body.b.from_)
    b_to = parse_iso_datetime(body.b.to)

    if a_to <= a_from:
        raise HTTPException(
            status_code=422,
            detail="a.to must be after a.from",
        )
    if b_to <= b_from:
        raise HTTPException(
            status_code=422,
            detail="b.to must be after b.from",
        )

    dur_a = a_to - a_from
    dur_b = b_to - b_from
    longer = dur_a if dur_a >= dur_b else dur_b
    # Each panel gets half the canvas width for aggregation choice.
    half_width = max(MIN_PIXEL_WIDTH, pixel_width // 2)
    unit = choose_aggregation(longer, half_width)

    # Bucket ceiling applies per range with the shared unit.
    _validate_range_bucket_ceiling(dur_a, unit)
    _validate_range_bucket_ceiling(dur_b, unit)

    scope_conds, scope_params = scope_filters(body.scope)
    lanes_a = _fetch_lanes_for_viewport(
        pool,
        unit=unit,
        viewport=body.a,
        lanes=body.lanes,
        scope=body.scope,
        scope_conds=scope_conds,
        scope_params=scope_params,
    )
    lanes_b = _fetch_lanes_for_viewport(
        pool,
        unit=unit,
        viewport=body.b,
        lanes=body.lanes,
        scope=body.scope,
        scope_conds=scope_conds,
        scope_params=scope_params,
    )

    def side_totals(lanes: dict[str, LanePayload]) -> CompareTotalsSide:
        messages = lanes.get("messages")
        attachments = lanes.get("attachments")
        msg_n = _lane_total(messages) if isinstance(messages, list) else 0
        att_n = _lane_total(attachments) if isinstance(attachments, list) else 0
        return CompareTotalsSide(messages=msg_n, attachments=att_n)

    fingerprint = scope_fingerprint(body.scope)
    aligned = durations_aligned(dur_a, dur_b)

    logger.info(
        "chronicle_compare",
        unit=unit,
        aligned=aligned,
        lanes=list(body.lanes),
        pixel_width=pixel_width,
        scope_fingerprint=fingerprint,
    )

    return CompareResponse(
        unit=unit,
        aligned=aligned,
        a=CompareSide(viewport=body.a, lanes=lanes_a),
        b=CompareSide(viewport=body.b, lanes=lanes_b),
        totals=CompareTotals(a=side_totals(lanes_a), b=side_totals(lanes_b)),
        scope_fingerprint=fingerprint,
    )


@router.post("/compare", response_model=CompareResponse)
def chronicle_compare(
    body: CompareRequest,
    request: Request,
    _user: str = Depends(require_user),
) -> CompareResponse:
    """Aligned / small-multiple comparison datasets for two date ranges."""
    pool: ConnectionPool = request.app.state.pool
    return get_compare(pool, body)

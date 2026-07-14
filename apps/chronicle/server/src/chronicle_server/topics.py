"""Topics subsystem: clustering, assignment, curation, and generation.

Deterministic k-means over an embedding sample produces centroid-backed
``app_topics``; one set-based full-corpus membership assignment; term-derived
labels with optional gateway polish; curation CRUD with manual precedence
(TA-005).

``POST /api/topics/generate`` is synchronous and may take minutes on a full
corpus (the set-based assignment scans every embedded email). Data Health
records the run via the ``topics_generate`` audit action.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import numpy as np
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from chronicle_server.auth import require_user
from chronicle_server.chronicle import VALID_UNITS, choose_aggregation
from chronicle_server.cursor import decode_cursor, encode_cursor
from chronicle_server.db import audit
from chronicle_server.gateway import ModelGateway
from chronicle_server.ids import decode_source_id, encode_source_id, msg_key_to_uuid
from chronicle_server.scope import QueryScope, scope_filters

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["topics"])

TopicOrigin = Literal["automatic", "curated", "manual"]
MemberOrigin = Literal["automatic", "manual"]

_MAX_KMEANS_ITERS = 25
_CENTROID_SHIFT_EPS = 1e-4
_EMBED_DIM = 768
_TOP_TERMS = 5
_LABEL_TERMS = 3
_MAX_LABEL_WORDS = 4
_MIN_TERM_LEN = 3
_MEMBERS_DEFAULT_LIMIT = 50
_MEMBERS_MAX_LIMIT = 200
_RIVER_DEFAULT_TOP = 8
_RIVER_MAX_TOP = 48
_RIVER_DEFAULT_PIXEL_WIDTH = 920

# Common English stopwords for subject TF labeling (lowercase).
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "re",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "fw",
        "fwd",
        "subject",
    }
)

_TERM_RE = re.compile(r"[a-z0-9]+")

LABEL_POLISH_POLICY = (
    "Given topic term lists, output ONLY a STRICT JSON array of short labels "
    "(one per topic, same order). Each label is at most 4 words. "
    "No other keys, no prose."
)


# --- request / response models ---


class GenerateRequest(BaseModel):
    k: int = Field(default=24, ge=4, le=48)
    sample: int = Field(default=20000, ge=1)
    seed: int = 13


class GenerateResponse(BaseModel):
    topics: int
    assigned: int
    skipped_curated: int
    took_ms: int


class TopicCreate(BaseModel):
    label: str = Field(min_length=1)
    description: str | None = None


class TopicPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    description: str | None = None
    hidden: bool | None = None
    parent_id: str | None = None

    @field_validator("parent_id")
    @classmethod
    def _empty_parent_ok(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == "":
            return None
        return value


class MemberAddRequest(BaseModel):
    email_sid: str = Field(min_length=1)


# --- pure helpers (testable without DB) ---


def pca_project_2d(centroids: np.ndarray) -> np.ndarray:
    """Deterministic PCA of centroid matrix to 2D via SVD.

    Returns an ``(n, 2)`` array with coordinates normalized into ``[-1, 1]``.
    Topics without centroids must be filtered out before calling.
    """
    arr = np.asarray(centroids, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return np.zeros((0, 2), dtype=np.float64)
    n, d = arr.shape
    if n == 1:
        return np.zeros((1, 2), dtype=np.float64)

    centered = arr - arr.mean(axis=0, keepdims=True)
    # Economy SVD is deterministic for a fixed matrix (numpy).
    _u, s, vt = np.linalg.svd(centered, full_matrices=False)
    k = min(2, s.shape[0], d, n)
    # Project: X_c @ V_k  (equivalent to U[:, :k] * S[:k])
    coords = centered @ vt[:k].T
    if k < 2:
        coords = np.pad(coords, ((0, 0), (0, 2 - k)))
    max_abs = float(np.max(np.abs(coords))) if coords.size else 0.0
    if max_abs > 0.0 and np.isfinite(max_abs):
        coords = coords / max_abs
    return np.asarray(coords, dtype=np.float64)


def _parse_viewport_dt(value: str) -> datetime:
    """Parse ISO datetime/date for viewport bounds; raise 422 on failure."""
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=422, detail="empty datetime")
    # Accept trailing Z.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _scope_from_query(
    *,
    mailboxes: list[str] | None,
    senders: list[str] | None,
    scope_from: str | None,
    scope_to: str | None,
) -> QueryScope:
    payload: dict[str, Any] = {
        "mailboxes": list(mailboxes or []),
        "senders": list(senders or []),
    }
    if scope_from or scope_to:
        date: dict[str, str] = {}
        if scope_from:
            date["from"] = scope_from
        if scope_to:
            date["to"] = scope_to
        payload["date"] = date
    return QueryScope.model_validate(payload)


def _resolve_river_unit(unit: str, viewport_from: datetime, viewport_to: datetime) -> str:
    if unit == "auto":
        duration = viewport_to - viewport_from
        if duration.total_seconds() <= 0:
            duration = timedelta(days=1)
        return choose_aggregation(duration, _RIVER_DEFAULT_PIXEL_WIDTH)
    if unit not in VALID_UNITS:
        raise HTTPException(
            status_code=422,
            detail=f"unit must be auto or one of {', '.join(VALID_UNITS)}",
        )
    return unit


def parse_embedding(raw: Any) -> list[float] | None:
    """Parse pgvector embedding (string or list) into floats."""
    if raw is None:
        return None
    if isinstance(raw, list | tuple | np.ndarray):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        return [float(x) for x in raw.strip("[]").split(",") if x.strip()]
    try:
        return [float(x) for x in raw]
    except (TypeError, ValueError):
        return None


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize; zero rows stay zero."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return np.asarray(vectors / norms, dtype=np.float64)


def kmeans_plus_plus_init(
    vectors: np.ndarray,
    k: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """k-means++ centroid initialization (seeded)."""
    n = vectors.shape[0]
    centroids = np.empty((k, vectors.shape[1]), dtype=np.float64)
    first = int(rng.integers(0, n))
    centroids[0] = vectors[first]
    # Squared Euclidean distances to nearest chosen centroid.
    closest = np.full(n, np.inf, dtype=np.float64)
    for i in range(1, k):
        diff = vectors - centroids[i - 1]
        dist_sq = np.sum(diff * diff, axis=1)
        closest = np.minimum(closest, dist_sq)
        total = float(closest.sum())
        if total <= 0.0 or not np.isfinite(total):
            centroids[i] = vectors[int(rng.integers(0, n))]
            continue
        probs = closest / total
        idx = int(rng.choice(n, p=probs))
        centroids[i] = vectors[idx]
    return centroids


def run_kmeans(
    vectors: np.ndarray,
    k: int,
    *,
    seed: int = 13,
    max_iters: int = _MAX_KMEANS_ITERS,
    eps: float = _CENTROID_SHIFT_EPS,
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic L2-normalized k-means (cosine-equivalent).

    Returns ``(centroids, labels)`` where centroids are L2-normalized and
    labels is an int array of length n (cluster index per sample).

    Raises ValueError when ``k > n`` or ``n == 0`` or ``k < 1``.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    n = vectors.shape[0]
    if n == 0:
        raise ValueError("empty sample")
    if k > n:
        raise ValueError(f"k ({k}) exceeds sample size ({n})")

    data = l2_normalize(np.asarray(vectors, dtype=np.float64))
    rng = np.random.default_rng(seed)
    centroids = l2_normalize(kmeans_plus_plus_init(data, k, rng))

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iters):
        # Assign: nearest centroid by squared Euclidean (≡ cosine on unit sphere).
        # (n, k) distances via ||x||^2 + ||c||^2 - 2 x·c; norms ≈ 1.
        dots = data @ centroids.T  # (n, k)
        dist_sq = 2.0 - 2.0 * dots
        labels = np.argmin(dist_sq, axis=1).astype(np.int32)

        new_centroids = np.zeros_like(centroids)
        for j in range(k):
            mask = labels == j
            if not np.any(mask):
                # Re-seed empty cluster from a random point.
                new_centroids[j] = data[int(rng.integers(0, n))]
            else:
                new_centroids[j] = data[mask].mean(axis=0)
        new_centroids = l2_normalize(new_centroids)

        shift = float(np.linalg.norm(new_centroids - centroids))
        centroids = new_centroids
        if shift < eps:
            break

    return centroids, labels


def extract_top_terms(
    subjects: list[str | None],
    *,
    top_n: int = _TOP_TERMS,
    stopwords: frozenset[str] = STOPWORDS,
) -> list[str]:
    """Top TF terms from subjects: lowercase, alnum ≥ 3, stopword-filtered."""
    counts: dict[str, int] = {}
    for subject in subjects:
        if not subject:
            continue
        for token in _TERM_RE.findall(subject.lower()):
            if len(token) < _MIN_TERM_LEN or token in stopwords:
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [term for term, _ in ranked[:top_n]]


def label_from_terms(terms: list[str], n: int = _LABEL_TERMS) -> str:
    """Comma-join of the first *n* terms, or a placeholder when empty."""
    chosen = terms[:n]
    if not chosen:
        return "untitled topic"
    return ", ".join(chosen)


def _largest_json_array(text: str) -> str | None:
    """Return the largest balanced ``[...]`` substring, or None."""
    best: str | None = None
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : i + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
    return best


def validate_label_array(raw: Any, expected: int) -> list[str] | None:
    """Whitelist-validate a JSON array of ≤4-word label strings.

    Returns None on any structural failure (wrong type, wrong length, empty).
    """
    if not isinstance(raw, list) or len(raw) != expected:
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        label = " ".join(item.split())
        if not label:
            return None
        words = label.split()
        if len(words) > _MAX_LABEL_WORDS:
            return None
        out.append(label)
    return out


def parse_label_polish_response(content: str, expected: int) -> list[str] | None:
    """Extract and validate gateway label array. None on failure."""
    if not content or not content.strip():
        return None
    block = _largest_json_array(content)
    if block is None:
        return None
    try:
        raw = json.loads(block)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return validate_label_array(raw, expected)


def vector_literal(vec: np.ndarray | list[float]) -> str:
    """Format a float vector as a pgvector text literal ``[a,b,...]``."""
    arr = np.asarray(vec, dtype=np.float64).ravel()
    return "[" + ",".join(f"{float(x):.8g}" for x in arr) + "]"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _gateway_from_request(request: Request, settings: ChronicleSettings) -> ModelGateway:
    transport = getattr(request.app.state, "chat_transport", None)
    return ModelGateway(settings, transport)


def _model_available(request: Request, gateway: ModelGateway) -> bool:
    forced = getattr(request.app.state, "model_available", None)
    if forced is not None:
        return bool(forced)
    return gateway.availability()


def _complete_chat(gateway: ModelGateway, messages: list[dict[str, str]]) -> str:
    """One non-streaming completion: collect all transport deltas."""
    settings = gateway._settings  # noqa: SLF001
    transport = gateway._transport  # noqa: SLF001
    parts: list[str] = []
    for delta in transport(settings.answer_model, messages, False):
        if delta:
            parts.append(str(delta))
    return "".join(parts)


def _topic_row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "label": str(row[1]),
        "description": row[2],
        "origin": str(row[3]),
        "parent_id": str(row[4]) if row[4] is not None else None,
        "hidden": bool(row[5]),
        "top_terms": list(row[6]) if row[6] is not None else [],
        "generation": int(row[7]),
        "member_count": int(row[8]),
        "created_at": _iso(row[9]),
        "updated_at": _iso(row[10]),
    }


_TOPIC_SELECT = """
    SELECT id, label, description, origin, parent_id, hidden,
           top_terms, generation, member_count, created_at, updated_at
      FROM app_topics
"""


# --- generation ---


def generate_topics(
    pool: ConnectionPool,
    body: GenerateRequest,
    *,
    username: str,
    gateway: ModelGateway | None = None,
    model_available: bool = False,
) -> GenerateResponse:
    """Sample embeddings, cluster, label, replace-regenerate, full-corpus assign.

    Runtime note: the full-corpus assignment is a single set-based scan of every
    email with an embedding and may take minutes on large archives. The endpoint
    is synchronous; Data Health surfaces the run via audit ``topics_generate``.
    """
    started = time.perf_counter()
    k = body.k
    sample_n = body.sample
    seed = body.seed

    with pool.connection() as conn:
        # Stable pseudo-random sample via md5(id::text) order (deterministic).
        sample_rows = conn.execute(
            """
            SELECT id, embedding, subject
              FROM emails
             WHERE embedding IS NOT NULL
             ORDER BY md5(id::text)
             LIMIT %(sample)s
            """,
            {"sample": sample_n},
        ).fetchall()

        if not sample_rows:
            audit(
                pool,
                username=username,
                action="topics_generate",
                detail={
                    "topics": 0,
                    "assigned": 0,
                    "skipped_curated": 0,
                    "seed": seed,
                    "k": k,
                    "sample": sample_n,
                    "took_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            return GenerateResponse(topics=0, assigned=0, skipped_curated=0, took_ms=0)

        ids: list[UUID] = []
        subjects: list[str | None] = []
        vectors_list: list[list[float]] = []
        for row in sample_rows:
            emb = parse_embedding(row[1])
            if emb is None or len(emb) != _EMBED_DIM:
                continue
            ids.append(row[0])
            subjects.append(row[2] if row[2] is not None else None)
            vectors_list.append(emb)

        if not vectors_list:
            took_ms = int((time.perf_counter() - started) * 1000)
            audit(
                pool,
                username=username,
                action="topics_generate",
                detail={
                    "topics": 0,
                    "assigned": 0,
                    "skipped_curated": 0,
                    "seed": seed,
                    "k": k,
                    "sample": sample_n,
                    "took_ms": took_ms,
                },
            )
            return GenerateResponse(topics=0, assigned=0, skipped_curated=0, took_ms=took_ms)

        n = len(vectors_list)
        effective_k = min(k, n)
        if k > n:
            # Guard: cannot form more clusters than samples; use n.
            logger.info("topics_k_clamped", requested_k=k, sample_n=n)
            effective_k = n

        vectors = np.asarray(vectors_list, dtype=np.float64)
        centroids, labels = run_kmeans(vectors, effective_k, seed=seed)

        # Per-cluster subjects from the sample → top terms + default labels.
        cluster_subjects: list[list[str | None]] = [[] for _ in range(effective_k)]
        for i, lab in enumerate(labels):
            cluster_subjects[int(lab)].append(subjects[i])

        top_terms_list: list[list[str]] = []
        term_labels: list[str] = []
        for j in range(effective_k):
            terms = extract_top_terms(cluster_subjects[j])
            top_terms_list.append(terms)
            term_labels.append(label_from_terms(terms))

        labels_final = list(term_labels)
        if model_available and gateway is not None and effective_k > 0:
            term_payload = [
                {"index": j, "terms": top_terms_list[j], "fallback": term_labels[j]}
                for j in range(effective_k)
            ]
            messages = [
                {"role": "system", "content": LABEL_POLISH_POLICY},
                {
                    "role": "user",
                    "content": json.dumps(term_payload, ensure_ascii=False),
                },
            ]
            try:
                content = _complete_chat(gateway, messages)
                polished = parse_label_polish_response(content, effective_k)
                if polished is not None:
                    labels_final = polished
            except Exception as exc:
                logger.info("topics_label_polish_failed", error=str(exc))

        # Surviving topics: curated/manual origin, hidden automatic, or any
        # with manual members — never deleted or relabeled (TA-005).
        surviving = conn.execute(
            """
            SELECT t.id
              FROM app_topics t
             WHERE t.origin IN ('curated', 'manual')
                OR t.hidden = TRUE
                OR EXISTS (
                    SELECT 1 FROM app_topic_members m
                     WHERE m.topic_id = t.id AND m.origin = 'manual'
                )
            """
        ).fetchall()
        surviving_ids = {row[0] for row in surviving}
        skipped_curated = len(surviving_ids)

        # Delete replaceable automatic topics (cascade memberships).
        conn.execute(
            """
            DELETE FROM app_topics t
             WHERE t.origin = 'automatic'
               AND t.hidden = FALSE
               AND NOT EXISTS (
                   SELECT 1 FROM app_topic_members m
                    WHERE m.topic_id = t.id AND m.origin = 'manual'
               )
            """
        )

        gen_row = conn.execute("SELECT COALESCE(MAX(generation), 0) FROM app_topics").fetchone()
        next_gen = int(gen_row[0]) + 1 if gen_row else 1

        new_topic_ids: list[UUID] = []
        for j in range(effective_k):
            centroid_lit = vector_literal(centroids[j])
            inserted = conn.execute(
                """
                INSERT INTO app_topics (
                    label, origin, centroid, top_terms, generation, member_count
                ) VALUES (
                    %(label)s, 'automatic', %(centroid)s::vector,
                    %(top_terms)s, %(generation)s, 0
                )
                RETURNING id
                """,
                {
                    "label": labels_final[j],
                    "centroid": centroid_lit,
                    "top_terms": top_terms_list[j],
                    "generation": next_gen,
                },
            ).fetchone()
            if inserted is None:
                raise RuntimeError("topic insert returned no id")
            new_topic_ids.append(inserted[0])

        # Drop previous automatic memberships on automatic topics before reassign.
        conn.execute(
            """
            DELETE FROM app_topic_members m
             USING app_topics t
             WHERE m.topic_id = t.id
               AND t.origin = 'automatic'
               AND m.origin = 'automatic'
            """
        )

        # Full-corpus nearest-centroid assignment (one set-based statement).
        assign_result = conn.execute(
            """
            INSERT INTO app_topic_members (topic_id, email_id, distance, origin)
            SELECT t.id, e.id, t.centroid <=> e.embedding, 'automatic'
              FROM emails e
              CROSS JOIN LATERAL (
                SELECT id, centroid FROM app_topics
                 WHERE origin = 'automatic' AND centroid IS NOT NULL
                 ORDER BY centroid <=> e.embedding
                 LIMIT 1
              ) t
             WHERE e.embedding IS NOT NULL
            ON CONFLICT (topic_id, email_id) DO NOTHING
            """
        )
        assigned = assign_result.rowcount if assign_result.rowcount is not None else 0

        # One grouped UPDATE for all member_counts.
        conn.execute(
            """
            UPDATE app_topics t
               SET member_count = c.cnt
              FROM (
                SELECT t2.id AS id, count(m.email_id)::int AS cnt
                  FROM app_topics t2
                  LEFT JOIN app_topic_members m ON m.topic_id = t2.id
                 GROUP BY t2.id
              ) c
             WHERE t.id = c.id
            """
        )

        topics_created = len(new_topic_ids)
        conn.commit()

    took_ms = int((time.perf_counter() - started) * 1000)
    audit(
        pool,
        username=username,
        action="topics_generate",
        detail={
            "topics": topics_created,
            "assigned": assigned,
            "skipped_curated": skipped_curated,
            "seed": seed,
            "k": k,
            "sample": sample_n,
            "took_ms": took_ms,
        },
    )
    logger.info(
        "topics_generate",
        topics=topics_created,
        assigned=assigned,
        skipped_curated=skipped_curated,
        took_ms=took_ms,
        seed=seed,
        k=k,
    )
    return GenerateResponse(
        topics=topics_created,
        assigned=assigned,
        skipped_curated=skipped_curated,
        took_ms=took_ms,
    )


# --- CRUD helpers ---


def list_topics(
    pool: ConnectionPool,
    *,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Return topics as a forest: parents with nested children.

    Visible by default (``hidden=false``); ``include_hidden`` shows all.
    """
    with pool.connection() as conn:
        if include_hidden:
            rows = conn.execute(
                _TOPIC_SELECT + " ORDER BY member_count DESC, label ASC, id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                _TOPIC_SELECT
                + " WHERE hidden = FALSE ORDER BY member_count DESC, label ASC, id ASC"
            ).fetchall()

    topics = [_topic_row_dict(r) for r in rows]
    by_id: dict[str, dict[str, Any]] = {
        t["id"]: {
            "id": t["id"],
            "label": t["label"],
            "origin": t["origin"],
            "member_count": t["member_count"],
            "hidden": t["hidden"],
            "top_terms": t["top_terms"],
            "parent_id": t["parent_id"],
            "children": [],
        }
        for t in topics
    }
    roots: list[dict[str, Any]] = []
    for t in topics:
        node = by_id[t["id"]]
        parent_id = t["parent_id"]
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    # Drop internal parent_id from API payload (tree structure carries it).
    def _strip(node: dict[str, Any]) -> dict[str, Any]:
        children = [_strip(c) for c in node["children"]]
        return {
            "id": node["id"],
            "label": node["label"],
            "origin": node["origin"],
            "member_count": node["member_count"],
            "hidden": node["hidden"],
            "top_terms": node["top_terms"],
            "children": children,
        }

    return [_strip(r) for r in roots]


def get_topic_detail(pool: ConnectionPool, topic_id: UUID) -> dict[str, Any]:
    """Topic detail + monthly activity series + 5 representative members."""
    with pool.connection() as conn:
        row = conn.execute(
            _TOPIC_SELECT + " WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Topic not found")

        topic = _topic_row_dict(row)

        activity_rows = conn.execute(
            """
            SELECT date_trunc('month', e.date) AS bucket, count(*)::int AS count
              FROM app_topic_members m
              JOIN emails e ON e.id = m.email_id
             WHERE m.topic_id = %(id)s
               AND e.date IS NOT NULL
             GROUP BY 1
             ORDER BY 1
            """,
            {"id": topic_id},
        ).fetchall()

        # Closest members when centroid exists; otherwise newest.
        if topic["origin"] != "manual":
            member_rows = conn.execute(
                """
                SELECT e.id, e.subject, e.sender_name, e.sender_address,
                       e.date, e.source_account, e.thread_id, m.distance
                  FROM app_topic_members m
                  JOIN emails e ON e.id = m.email_id
                  JOIN app_topics t ON t.id = m.topic_id
                 WHERE m.topic_id = %(id)s
                 ORDER BY m.distance ASC NULLS LAST, e.date DESC NULLS LAST
                 LIMIT 5
                """,
                {"id": topic_id},
            ).fetchall()
        else:
            member_rows = conn.execute(
                """
                SELECT e.id, e.subject, e.sender_name, e.sender_address,
                       e.date, e.source_account, e.thread_id, m.distance
                  FROM app_topic_members m
                  JOIN emails e ON e.id = m.email_id
                 WHERE m.topic_id = %(id)s
                 ORDER BY e.date DESC NULLS LAST
                 LIMIT 5
                """,
                {"id": topic_id},
            ).fetchall()

    activity = [
        {"bucket": _iso(r[0]), "count": int(r[1])} for r in activity_rows if r[0] is not None
    ]
    members = [
        {
            "id": encode_source_id("msg", r[0]),
            "subject": r[1],
            "sender_name": r[2],
            "sender_address": r[3],
            "date": _iso(r[4]),
            "mailbox": r[5],
            "thread_id": r[6],
            "distance": float(r[7]) if r[7] is not None else None,
        }
        for r in member_rows
    ]
    return {
        **{
            "id": topic["id"],
            "label": topic["label"],
            "description": topic["description"],
            "origin": topic["origin"],
            "parent_id": topic["parent_id"],
            "hidden": topic["hidden"],
            "top_terms": topic["top_terms"],
            "generation": topic["generation"],
            "member_count": topic["member_count"],
            "created_at": topic["created_at"],
            "updated_at": topic["updated_at"],
        },
        "activity": activity,
        "members": members,
    }


def patch_topic(
    pool: ConnectionPool,
    topic_id: UUID,
    body: TopicPatch,
    *,
    username: str,
) -> dict[str, Any]:
    """Rename/describe/hide/parent; automatic → curated on any change (Table 21)."""
    fields: list[str] = []
    params: dict[str, Any] = {"id": topic_id}

    if body.label is not None:
        fields.append("label = %(label)s")
        params["label"] = body.label
    if body.description is not None:
        fields.append("description = %(description)s")
        params["description"] = body.description
    if body.hidden is not None:
        fields.append("hidden = %(hidden)s")
        params["hidden"] = body.hidden
    if "parent_id" in body.model_fields_set:
        if body.parent_id is None:
            fields.append("parent_id = NULL")
        else:
            try:
                parent_uuid = UUID(body.parent_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="invalid parent_id") from exc
            if parent_uuid == topic_id:
                raise HTTPException(status_code=422, detail="parent_id cannot be self")
            fields.append("parent_id = %(parent_id)s")
            params["parent_id"] = parent_uuid

    if not fields:
        raise HTTPException(status_code=422, detail="no fields to update")

    with pool.connection() as conn:
        existing = conn.execute(
            "SELECT origin FROM app_topics WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Topic not found")

        if "parent_id" in params:
            parent_row = conn.execute(
                "SELECT 1 FROM app_topics WHERE id = %(parent_id)s",
                {"parent_id": params["parent_id"]},
            ).fetchone()
            if parent_row is None:
                raise HTTPException(status_code=404, detail="Parent topic not found")

        origin = str(existing[0])
        if origin == "automatic":
            fields.append("origin = 'curated'")

        fields.append("updated_at = now()")
        sql = f"UPDATE app_topics SET {', '.join(fields)} WHERE id = %(id)s RETURNING id"
        conn.execute(sql, params)
        conn.commit()

    audit(
        pool,
        username=username,
        action="topics_patch",
        detail={"topic_id": str(topic_id), "fields": list(body.model_fields_set)},
    )
    return get_topic_detail(pool, topic_id)


def create_manual_topic(
    pool: ConnectionPool,
    body: TopicCreate,
    *,
    username: str,
) -> dict[str, Any]:
    """Create a manual topic (no centroid; members added manually)."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_topics (label, description, origin)
            VALUES (%(label)s, %(description)s, 'manual')
            RETURNING id
            """,
            {"label": body.label, "description": body.description},
        ).fetchone()
        assert row is not None
        topic_id = row[0]
        conn.commit()

    audit(
        pool,
        username=username,
        action="topics_create",
        detail={"topic_id": str(topic_id), "label": body.label},
    )
    return get_topic_detail(pool, topic_id)


def delete_topic(
    pool: ConnectionPool,
    topic_id: UUID,
    *,
    username: str,
) -> dict[str, str]:
    """Hard-delete only origin='manual'; others → 403 (hide instead)."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT origin FROM app_topics WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        if str(row[0]) != "manual":
            raise HTTPException(
                status_code=403,
                detail="Only manual topics can be deleted; hide instead",
            )
        conn.execute("DELETE FROM app_topics WHERE id = %(id)s", {"id": topic_id})
        conn.commit()

    audit(
        pool,
        username=username,
        action="topics_delete",
        detail={"topic_id": str(topic_id)},
    )
    return {"status": "deleted"}


def add_member(
    pool: ConnectionPool,
    topic_id: UUID,
    email_sid: str,
    *,
    username: str,
) -> dict[str, Any]:
    """Manual include: insert membership with origin='manual'."""
    try:
        kind, key = decode_source_id(email_sid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown source_id: {email_sid}") from exc
    if kind != "msg" or not isinstance(key, int):
        raise HTTPException(status_code=422, detail="email_sid must be a msg_ source id")
    email_id = msg_key_to_uuid(key)

    with pool.connection() as conn:
        trow = conn.execute(
            "SELECT 1 FROM app_topics WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if trow is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        erow = conn.execute(
            "SELECT 1 FROM emails WHERE id = %(id)s",
            {"id": email_id},
        ).fetchone()
        if erow is None:
            raise HTTPException(status_code=404, detail=f"Unknown source_id: {email_sid}")

        conn.execute(
            """
            INSERT INTO app_topic_members (topic_id, email_id, distance, origin)
            VALUES (%(topic_id)s, %(email_id)s, NULL, 'manual')
            ON CONFLICT (topic_id, email_id) DO UPDATE
              SET origin = 'manual',
                  distance = COALESCE(app_topic_members.distance, EXCLUDED.distance)
            """,
            {"topic_id": topic_id, "email_id": email_id},
        )
        conn.execute(
            """
            UPDATE app_topics
               SET member_count = (
                   SELECT count(*)::int FROM app_topic_members WHERE topic_id = %(id)s
               ),
                   updated_at = now()
             WHERE id = %(id)s
            """,
            {"id": topic_id},
        )
        conn.commit()

    audit(
        pool,
        username=username,
        action="topics_member_add",
        detail={"topic_id": str(topic_id), "email_sid": email_sid},
    )
    return {"status": "ok", "topic_id": str(topic_id), "email_sid": email_sid}


def remove_member(
    pool: ConnectionPool,
    topic_id: UUID,
    email_sid: str,
    *,
    username: str,
) -> dict[str, Any]:
    """Exclude: DELETE membership row (manual or automatic)."""
    try:
        kind, key = decode_source_id(email_sid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown source_id: {email_sid}") from exc
    if kind != "msg" or not isinstance(key, int):
        raise HTTPException(status_code=422, detail="email_sid must be a msg_ source id")
    email_id = msg_key_to_uuid(key)

    with pool.connection() as conn:
        trow = conn.execute(
            "SELECT 1 FROM app_topics WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if trow is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        deleted = conn.execute(
            """
            DELETE FROM app_topic_members
             WHERE topic_id = %(topic_id)s AND email_id = %(email_id)s
            """,
            {"topic_id": topic_id, "email_id": email_id},
        )
        if deleted.rowcount == 0:
            raise HTTPException(status_code=404, detail="Membership not found")
        conn.execute(
            """
            UPDATE app_topics
               SET member_count = (
                   SELECT count(*)::int FROM app_topic_members WHERE topic_id = %(id)s
               ),
                   updated_at = now()
             WHERE id = %(id)s
            """,
            {"id": topic_id},
        )
        conn.commit()

    audit(
        pool,
        username=username,
        action="topics_member_remove",
        detail={"topic_id": str(topic_id), "email_sid": email_sid},
    )
    return {"status": "ok", "topic_id": str(topic_id), "email_sid": email_sid}


# --- Atlas analytics (river / matrix / projection / members) ---


def topics_river(
    pool: ConnectionPool,
    *,
    viewport_from: str,
    viewport_to: str,
    unit: str = "auto",
    top: int = _RIVER_DEFAULT_TOP,
    scope: QueryScope | None = None,
) -> dict[str, Any]:
    """Per-topic bucket series for the top-N visible topics in range.

    Reuses the Chronicle topics-lane ranked CTE shape with parameterized ``top``.
    ``mode_hint`` is always ``absolute`` — normalization is client-side.
    """
    top_n = max(1, min(int(top), _RIVER_MAX_TOP))
    vf = _parse_viewport_dt(viewport_from)
    vt = _parse_viewport_dt(viewport_to)
    if not (vt > vf):
        raise HTTPException(status_code=422, detail="to must be after from")
    resolved_unit = _resolve_river_unit(unit, vf, vt)
    scope = scope or QueryScope()
    scope_conds, scope_params = scope_filters(scope)

    # Bare emails columns are unambiguous (members/topics lack those names).
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
        "unit": resolved_unit,
        "viewport_from": vf.isoformat(),
        "viewport_to": vt.isoformat(),
        "top_limit": top_n,
    }
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    by_topic: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        topic_id = str(row[0])
        label = str(row[1]) if row[1] is not None else topic_id
        origin = str(row[2])
        if topic_id not in by_topic:
            by_topic[topic_id] = {
                "topic_id": topic_id,
                "label": label,
                "origin": origin,
                "buckets": [],
            }
            order.append(topic_id)
        bucket = _iso(row[3])
        if bucket is None:
            continue
        by_topic[topic_id]["buckets"].append({"bucket": bucket, "count": int(row[4])})

    return {
        "unit": resolved_unit,
        "mode_hint": "absolute",
        "from": _iso(vf),
        "to": _iso(vt),
        "topics": [by_topic[tid] for tid in order],
    }


def topics_matrix(
    pool: ConnectionPool,
    *,
    by: str = "year",
    scope: QueryScope | None = None,
) -> dict[str, Any]:
    """Visible topics × year member counts (one grouped statement) + totals."""
    if by != "year":
        raise HTTPException(status_code=422, detail="only by=year is supported")
    scope = scope or QueryScope()
    scope_conds, scope_params = scope_filters(scope)
    extra_sql = (" AND " + " AND ".join(scope_conds)) if scope_conds else ""

    sql = f"""
        SELECT
            t.id::text AS topic_id,
            t.label,
            t.origin,
            extract(year FROM e.date)::int AS year,
            count(*)::int AS count
          FROM app_topics t
          JOIN app_topic_members m ON m.topic_id = t.id
          JOIN emails e ON e.id = m.email_id
         WHERE t.hidden = FALSE
           AND e.date IS NOT NULL
           {extra_sql}
         GROUP BY t.id, t.label, t.origin, extract(year FROM e.date)
         ORDER BY t.member_count DESC, t.label ASC, year ASC
    """
    with pool.connection() as conn:
        rows = conn.execute(sql, scope_params).fetchall()

    # topic_id → {label, origin, cells: {year: count}}
    topics_map: dict[str, dict[str, Any]] = {}
    years_set: set[int] = set()
    for row in rows:
        tid = str(row[0])
        year = int(row[3])
        count = int(row[4])
        years_set.add(year)
        if tid not in topics_map:
            topics_map[tid] = {
                "topic_id": tid,
                "label": str(row[1]),
                "origin": str(row[2]),
                "cells": {},
            }
        topics_map[tid]["cells"][str(year)] = count

    years = sorted(years_set)
    rows_out: list[dict[str, Any]] = []
    col_totals: dict[str, int] = {str(y): 0 for y in years}
    grand_total = 0
    for tid, info in topics_map.items():
        row_total = 0
        cells: dict[str, int] = {}
        for y in years:
            c = int(info["cells"].get(str(y), 0))
            cells[str(y)] = c
            row_total += c
            col_totals[str(y)] = col_totals[str(y)] + c
        grand_total += row_total
        rows_out.append(
            {
                "topic_id": tid,
                "label": info["label"],
                "origin": info["origin"],
                "cells": cells,
                "row_total": row_total,
            }
        )

    return {
        "by": "year",
        "columns": [str(y) for y in years],
        "rows": rows_out,
        "column_totals": col_totals,
        "grand_total": grand_total,
    }


def topics_projection(pool: ConnectionPool) -> dict[str, Any]:
    """Centroid PCA (numpy SVD) of automatic+curated topics to 2D.

    Manual topics (no centroid) are excluded; response notes the count.
    NEVER returns per-source points (TA-003 — LOD stops at topics).
    """
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, label, origin, member_count, centroid
              FROM app_topics
             WHERE hidden = FALSE
               AND centroid IS NOT NULL
               AND origin IN ('automatic', 'curated')
             ORDER BY member_count DESC, label ASC, id ASC
            """
        ).fetchall()
        excluded_row = conn.execute(
            """
            SELECT count(*)::int
              FROM app_topics
             WHERE hidden = FALSE
               AND (centroid IS NULL OR origin = 'manual')
            """
        ).fetchone()
    excluded_manual = int(excluded_row[0]) if excluded_row else 0

    topic_meta: list[tuple[str, str, str, int]] = []
    vectors: list[list[float]] = []
    for row in rows:
        emb = parse_embedding(row[4])
        if emb is None or len(emb) != _EMBED_DIM:
            excluded_manual += 1
            continue
        topic_meta.append((str(row[0]), str(row[1]), str(row[2]), int(row[3])))
        vectors.append(emb)

    points: list[dict[str, Any]] = []
    if vectors:
        coords = pca_project_2d(np.asarray(vectors, dtype=np.float64))
        for i, (tid, label, origin, mcount) in enumerate(topic_meta):
            points.append(
                {
                    "topic_id": tid,
                    "label": label,
                    "origin": origin,
                    "member_count": mcount,
                    "x": float(coords[i, 0]),
                    "y": float(coords[i, 1]),
                }
            )

    note = None
    if excluded_manual > 0:
        note = (
            f"{excluded_manual} topic(s) excluded (manual or missing centroid); "
            "projection is topic-level only — no per-source points (TA-003)."
        )
    else:
        note = "Projection is topic-level only — no per-source points (TA-003)."

    return {
        "points": points,
        "excluded_without_centroid": excluded_manual,
        "note": note,
    }


def _parse_members_cursor(token: str, secret_key: str) -> tuple[str | None, UUID]:
    try:
        payload = decode_cursor(token, secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if "id" not in payload:
        raise HTTPException(status_code=400, detail="invalid cursor")
    try:
        last_id = UUID(str(payload["id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    d = payload.get("d")
    if d is not None and not isinstance(d, str):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return d if isinstance(d, str) else None, last_id


def list_topic_members(
    pool: ConnectionPool,
    topic_id: UUID,
    *,
    secret_key: str,
    cursor: str | None = None,
    limit: int = _MEMBERS_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Paginated definitive member list for a topic (keyset on date DESC, id DESC).

    Atlas "Open sources" uses this instead of Research Desk topic scoping
    (Phase 5 polish). TA-004 — every aggregated view can open this list.
    """
    lim = max(1, min(int(limit), _MEMBERS_MAX_LIMIT))
    with pool.connection() as conn:
        trow = conn.execute(
            "SELECT 1 FROM app_topics WHERE id = %(id)s",
            {"id": topic_id},
        ).fetchone()
        if trow is None:
            raise HTTPException(status_code=404, detail="Topic not found")

        conditions = ["m.topic_id = %(topic_id)s"]
        params: dict[str, Any] = {"topic_id": topic_id, "lim": lim + 1}

        if cursor:
            cursor_d, cursor_id = _parse_members_cursor(cursor, secret_key)
            params["cursor_id"] = cursor_id
            if cursor_d is not None:
                params["cursor_d"] = cursor_d
                # DESC NULLS LAST: after (d, id) come earlier dates, same-date lower ids,
                # then all NULL dates.
                conditions.append(
                    "("
                    " (e.date IS NOT NULL AND (e.date < %(cursor_d)s"
                    "  OR (e.date = %(cursor_d)s AND e.id < %(cursor_id)s)))"
                    " OR e.date IS NULL"
                    ")"
                )
            else:
                conditions.append("e.date IS NULL AND e.id < %(cursor_id)s")

        where_sql = " AND ".join(conditions)
        rows = conn.execute(
            f"""
            SELECT e.id, e.subject, e.sender_name, e.sender_address,
                   e.date, e.source_account, e.thread_id, m.distance
              FROM app_topic_members m
              JOIN emails e ON e.id = m.email_id
             WHERE {where_sql}
             ORDER BY e.date DESC NULLS LAST, e.id DESC
             LIMIT %(lim)s
            """,
            params,
        ).fetchall()

    page = rows[:lim]
    has_more = len(rows) > lim
    items = [
        {
            "id": encode_source_id("msg", r[0]),
            "subject": r[1],
            "sender_name": r[2],
            "sender_address": r[3],
            "date": _iso(r[4]),
            "mailbox": r[5],
            "thread_id": r[6],
            "distance": float(r[7]) if r[7] is not None else None,
        }
        for r in page
    ]
    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(
            {"d": _iso(last[4]), "id": str(last[0])},
            secret_key,
        )
    return {"items": items, "next_cursor": next_cursor}


# --- routes ---


@router.post("/topics/generate", response_model=GenerateResponse)
def topics_generate(
    body: GenerateRequest,
    request: Request,
    user: str = Depends(require_user),
) -> GenerateResponse:
    """Cluster sample + full-corpus assign. Synchronous; may take minutes."""
    pool: ConnectionPool = request.app.state.pool
    settings: ChronicleSettings = request.app.state.settings
    gateway = _gateway_from_request(request, settings)
    available = _model_available(request, gateway)
    return generate_topics(
        pool,
        body,
        username=user,
        gateway=gateway if available else None,
        model_available=available,
    )


@router.get("/topics")
def topics_list(
    request: Request,
    include_hidden: bool = Query(default=False),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Topic tree (parents + children); visible by default."""
    pool: ConnectionPool = request.app.state.pool
    return {"topics": list_topics(pool, include_hidden=include_hidden)}


@router.get("/topics/river")
def topics_river_endpoint(
    request: Request,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    unit: str = Query(default="auto"),
    top: int = Query(default=_RIVER_DEFAULT_TOP, ge=1, le=_RIVER_MAX_TOP),
    mb: str | None = Query(default=None, description="CSV mailboxes"),
    sd: str | None = Query(default=None, description="CSV senders"),
    df: str | None = Query(default=None, description="Scope date from"),
    dt: str | None = Query(default=None, description="Scope date to"),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Topic river: top-N visible topics × time buckets (mode_hint=absolute)."""
    pool: ConnectionPool = request.app.state.pool
    mailboxes = [s.strip() for s in (mb or "").split(",") if s.strip()]
    senders = [s.strip() for s in (sd or "").split(",") if s.strip()]
    scope = _scope_from_query(
        mailboxes=mailboxes,
        senders=senders,
        scope_from=df,
        scope_to=dt,
    )
    return topics_river(
        pool,
        viewport_from=from_,
        viewport_to=to,
        unit=unit,
        top=top,
        scope=scope,
    )


@router.get("/topics/matrix")
def topics_matrix_endpoint(
    request: Request,
    by: str = Query(default="year"),
    mb: str | None = Query(default=None, description="CSV mailboxes"),
    sd: str | None = Query(default=None, description="CSV senders"),
    df: str | None = Query(default=None, description="Scope date from"),
    dt: str | None = Query(default=None, description="Scope date to"),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Topic × year matrix with row/column totals."""
    pool: ConnectionPool = request.app.state.pool
    mailboxes = [s.strip() for s in (mb or "").split(",") if s.strip()]
    senders = [s.strip() for s in (sd or "").split(",") if s.strip()]
    scope = _scope_from_query(
        mailboxes=mailboxes,
        senders=senders,
        scope_from=df,
        scope_to=dt,
    )
    return topics_matrix(pool, by=by, scope=scope)


@router.get("/topics/projection")
def topics_projection_endpoint(
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Centroid PCA to 2D; topics only — never per-source points (TA-003)."""
    pool: ConnectionPool = request.app.state.pool
    return topics_projection(pool)


@router.post("/topics")
def topics_create(
    body: TopicCreate,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Create a manual topic."""
    pool: ConnectionPool = request.app.state.pool
    return create_manual_topic(pool, body, username=user)


@router.get("/topics/{topic_id}/members")
def topics_members_list(
    topic_id: UUID,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_MEMBERS_DEFAULT_LIMIT, ge=1, le=_MEMBERS_MAX_LIMIT),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Paginated definitive member list (keyset). Atlas TA-004 source list."""
    pool: ConnectionPool = request.app.state.pool
    secret_key: str = request.app.state.settings.secret_key
    return list_topic_members(
        pool,
        topic_id,
        secret_key=secret_key,
        cursor=cursor,
        limit=limit,
    )


@router.get("/topics/{topic_id}")
def topics_get(
    topic_id: UUID,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Topic detail, monthly activity, representative members."""
    pool: ConnectionPool = request.app.state.pool
    return get_topic_detail(pool, topic_id)


@router.patch("/topics/{topic_id}")
def topics_patch(
    topic_id: UUID,
    body: TopicPatch,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Rename/describe/hide/parent; flips automatic → curated."""
    pool: ConnectionPool = request.app.state.pool
    return patch_topic(pool, topic_id, body, username=user)


@router.delete("/topics/{topic_id}")
def topics_delete(
    topic_id: UUID,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, str]:
    """Hard-delete manual topics only."""
    pool: ConnectionPool = request.app.state.pool
    return delete_topic(pool, topic_id, username=user)


@router.post("/topics/{topic_id}/members")
def topics_member_add(
    topic_id: UUID,
    body: MemberAddRequest,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Manual membership include."""
    pool: ConnectionPool = request.app.state.pool
    return add_member(pool, topic_id, body.email_sid, username=user)


@router.delete("/topics/{topic_id}/members/{email_sid}")
def topics_member_remove(
    topic_id: UUID,
    email_sid: str,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Remove membership (manual exclude)."""
    pool: ConnectionPool = request.app.state.pool
    return remove_member(pool, topic_id, email_sid, username=user)

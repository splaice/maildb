"""Contacts subsystem: materialize address book + human-probability classifier.

The classifier never writes ``kind`` / ``kind_source`` — those are manual.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import structlog

from maildb.config import Settings

if TYPE_CHECKING:
    from uuid import UUID

    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

# Signal weights — deterministic, documented for explainability.
BASE_PROBABILITY = 0.50  # Prior before any address-level evidence.
WEIGHT_BIDIRECTIONAL = 0.35  # Both sent and received with the user.
WEIGHT_USER_INITIATED = 0.15  # User has written to this address at least once.
WEIGHT_PERSONAL_NAME = 0.10  # Display name looks like a personal first+last.
WEIGHT_AUTOMATED_PATTERN = -0.40  # Local-part matches noreply/billing/etc.
WEIGHT_LIST_PATTERN = -0.30  # Address looks like a mailing list.
WEIGHT_ONE_WAY_BULK = -0.20  # Many inbound messages, zero outbound.

_PERSONAL_NAME_RE = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$")
_NON_PERSONAL_NAME_RE = re.compile(
    r"\b(list|team|support|noreply|newsletter|admin|mailer|billing|notifications?)\b",
    re.IGNORECASE,
)
_AUTOMATED_LOCAL_RE = re.compile(
    r"(?:no-?reply|donotreply|do-not-reply|notifications?|alerts?|mailer-daemon|"
    r"postmaster|bounce|newsletters?|marketing|updates|receipts?|billing|invoices?)",
    re.IGNORECASE,
)
_LIST_ADDRESS_RE = re.compile(
    r"lists\.|googlegroups\.com|listserv|-announce@|-dev@|-users@",
    re.IGNORECASE,
)


def _normalize_address(address: str) -> str:
    return address.lower().strip()


def _user_identities(pool: ConnectionPool) -> list[str]:
    """Configured user_emails (+ legacy user_email) union imports.source_account."""
    settings = Settings()
    with pool.connection() as conn:
        cur = conn.execute("SELECT DISTINCT source_account FROM imports")
        ingested = [r[0] for r in cur.fetchall() if r[0]]
    seen: set[str] = set()
    merged: list[str] = []
    for addr in (*settings.user_emails, *ingested):
        if not addr:
            continue
        norm = _normalize_address(addr)
        if norm not in seen:
            seen.add(norm)
            merged.append(norm)
    return merged


def build_contacts(
    pool: ConnectionPool,
    *,
    import_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Materialize/refresh contact_addresses + singleton contacts from emails.

    With ``import_id``, only addresses appearing in that import are touched;
    stats for those addresses are recomputed from the full corpus. Without,
    the whole corpus is processed.

    Returns ``{"addresses": n, "contacts_created": n, "contact_ids": [...]}``.
    """
    user_ids = _user_identities(pool)
    with pool.connection() as conn:
        # Addresses touched by the (scoped) source rows.
        cur = conn.execute(
            """
            WITH scoped AS (
                SELECT sender_address, recipients
                  FROM emails
                 WHERE (%(import_id)s::uuid IS NULL OR import_id = %(import_id)s::uuid)
            ),
            from_addrs AS (
                SELECT lower(btrim(sender_address)) AS address
                  FROM scoped
                 WHERE sender_address IS NOT NULL AND btrim(sender_address) <> ''
            ),
            to_addrs AS (
                SELECT lower(btrim(r.addr)) AS address
                  FROM scoped e
                  CROSS JOIN LATERAL (
                      SELECT jsonb_array_elements_text(
                                 COALESCE(e.recipients->'to', '[]'::jsonb)
                             ) AS addr
                      UNION ALL
                      SELECT jsonb_array_elements_text(
                                 COALESCE(e.recipients->'cc', '[]'::jsonb)
                             )
                      UNION ALL
                      SELECT jsonb_array_elements_text(
                                 COALESCE(e.recipients->'bcc', '[]'::jsonb)
                             )
                  ) r
                 WHERE e.recipients IS NOT NULL
                   AND r.addr IS NOT NULL
                   AND btrim(r.addr) <> ''
            )
            SELECT DISTINCT address FROM from_addrs
            UNION
            SELECT DISTINCT address FROM to_addrs
            """,
            {"import_id": import_id},
        )
        touched = [row[0] for row in cur.fetchall()]
        if not touched:
            return {"addresses": 0, "contacts_created": 0, "contact_ids": []}

        # Full-corpus stats for touched addresses.
        cur = conn.execute(
            """
            WITH touched AS (
                SELECT unnest(%(addrs)s::text[]) AS address
            ),
            sender_names AS (
                SELECT lower(btrim(e.sender_address)) AS address,
                       e.sender_name,
                       count(*) AS cnt
                  FROM emails e
                  JOIN touched t ON lower(btrim(e.sender_address)) = t.address
                 WHERE e.sender_name IS NOT NULL AND btrim(e.sender_name) <> ''
                 GROUP BY 1, 2
            ),
            name_variants AS (
                SELECT address,
                       array_agg(sender_name ORDER BY sender_name) AS name_variants
                  FROM sender_names
                 GROUP BY address
            ),
            top_names AS (
                SELECT DISTINCT ON (address) address, sender_name AS top_name
                  FROM sender_names
                 ORDER BY address, cnt DESC, sender_name
            ),
            from_stats AS (
                SELECT lower(btrim(e.sender_address)) AS address,
                       count(*)::int AS messages_from,
                       min(e.date) AS first_seen,
                       max(e.date) AS last_seen
                  FROM emails e
                  JOIN touched t ON lower(btrim(e.sender_address)) = t.address
                 GROUP BY 1
            ),
            to_stats AS (
                SELECT t.address,
                       count(*)::int AS messages_to
                  FROM touched t
                  JOIN emails e ON (
                      lower(btrim(e.sender_address)) = ANY(%(user_emails)s)
                      AND (
                          e.recipients @> jsonb_build_object(
                              'to', to_jsonb(ARRAY[t.address])
                          )
                          OR e.recipients @> jsonb_build_object(
                              'cc', to_jsonb(ARRAY[t.address])
                          )
                          OR e.recipients @> jsonb_build_object(
                              'bcc', to_jsonb(ARRAY[t.address])
                          )
                      )
                  )
                 GROUP BY t.address
            ),
            date_bounds AS (
                -- first/last seen across both sent and received for recipient-only addrs
                SELECT t.address,
                       least(fs.first_seen, rs.first_seen) AS first_seen,
                       greatest(fs.last_seen, rs.last_seen) AS last_seen
                  FROM touched t
                  LEFT JOIN from_stats fs ON fs.address = t.address
                  LEFT JOIN (
                      SELECT t2.address,
                             min(e.date) AS first_seen,
                             max(e.date) AS last_seen
                        FROM touched t2
                        JOIN emails e ON (
                            e.recipients @> jsonb_build_object(
                                'to', to_jsonb(ARRAY[t2.address])
                            )
                            OR e.recipients @> jsonb_build_object(
                                'cc', to_jsonb(ARRAY[t2.address])
                            )
                            OR e.recipients @> jsonb_build_object(
                                'bcc', to_jsonb(ARRAY[t2.address])
                            )
                        )
                       GROUP BY t2.address
                  ) rs ON rs.address = t.address
            )
            SELECT t.address,
                   coalesce(nv.name_variants, '{}'::text[]) AS name_variants,
                   tn.top_name,
                   coalesce(fs.messages_from, 0) AS messages_from,
                   coalesce(ts.messages_to, 0) AS messages_to,
                   db.first_seen,
                   db.last_seen,
                   (t.address = ANY(%(user_emails)s)) AS is_user
              FROM touched t
              LEFT JOIN name_variants nv ON nv.address = t.address
              LEFT JOIN top_names tn ON tn.address = t.address
              LEFT JOIN from_stats fs ON fs.address = t.address
              LEFT JOIN to_stats ts ON ts.address = t.address
              LEFT JOIN date_bounds db ON db.address = t.address
            """,
            {"addrs": touched, "user_emails": user_ids},
        )
        rows = cur.fetchall()

        contacts_created = 0
        contact_ids: list[UUID] = []
        for (
            address,
            name_variants,
            top_name,
            messages_from,
            messages_to,
            first_seen,
            last_seen,
            is_user,
        ) in rows:
            existing = conn.execute(
                "SELECT contact_id FROM contact_addresses WHERE address = %(addr)s",
                {"addr": address},
            ).fetchone()
            if existing is None:
                inserted = conn.execute(
                    """INSERT INTO contacts (display_name)
                       VALUES (%(display_name)s)
                       RETURNING id""",
                    {"display_name": top_name},
                ).fetchone()
                if inserted is None:
                    msg = "INSERT INTO contacts RETURNING id produced no row"
                    raise RuntimeError(msg)
                contact_id = inserted[0]
                conn.execute(
                    """INSERT INTO contact_addresses (
                           address, contact_id, name_variants, is_user,
                           first_seen, last_seen, messages_from, messages_to
                       ) VALUES (
                           %(address)s, %(contact_id)s, %(name_variants)s, %(is_user)s,
                           %(first_seen)s, %(last_seen)s, %(messages_from)s, %(messages_to)s
                       )""",
                    {
                        "address": address,
                        "contact_id": contact_id,
                        "name_variants": name_variants or [],
                        "is_user": is_user,
                        "first_seen": first_seen,
                        "last_seen": last_seen,
                        "messages_from": messages_from,
                        "messages_to": messages_to,
                    },
                )
                contacts_created += 1
                contact_ids.append(contact_id)
            else:
                contact_id = existing[0]
                conn.execute(
                    """UPDATE contact_addresses
                          SET name_variants = %(name_variants)s,
                              is_user = %(is_user)s,
                              first_seen = %(first_seen)s,
                              last_seen = %(last_seen)s,
                              messages_from = %(messages_from)s,
                              messages_to = %(messages_to)s
                        WHERE address = %(address)s""",
                    {
                        "address": address,
                        "name_variants": name_variants or [],
                        "is_user": is_user,
                        "first_seen": first_seen,
                        "last_seen": last_seen,
                        "messages_from": messages_from,
                        "messages_to": messages_to,
                    },
                )
                contact_ids.append(contact_id)

        conn.commit()

    # Dedupe contact_ids while preserving order
    seen_ids: set[UUID] = set()
    unique_ids: list[UUID] = []
    for cid in contact_ids:
        if cid not in seen_ids:
            seen_ids.add(cid)
            unique_ids.append(cid)

    logger.info(
        "contacts_built",
        addresses=len(rows),
        contacts_created=contacts_created,
        import_id=str(import_id) if import_id else None,
    )
    return {
        "addresses": len(rows),
        "contacts_created": contacts_created,
        "contact_ids": unique_ids,
    }


def _is_personal_name(name: str) -> bool:
    if not _PERSONAL_NAME_RE.match(name.strip()):
        return False
    if _NON_PERSONAL_NAME_RE.search(name):
        return False
    return not any(ch.isdigit() for ch in name)


def _compute_probability(
    *,
    messages_from: int,
    messages_to: int,
    name_variants: list[str],
    addresses: list[str],
) -> tuple[float, dict[str, float]]:
    score = BASE_PROBABILITY
    signals: dict[str, float] = {}

    if messages_from > 0 and messages_to > 0:
        score += WEIGHT_BIDIRECTIONAL
        signals["bidirectional"] = WEIGHT_BIDIRECTIONAL
    if messages_to > 0:
        score += WEIGHT_USER_INITIATED
        signals["user_initiated"] = WEIGHT_USER_INITIATED
    if any(_is_personal_name(n) for n in name_variants):
        score += WEIGHT_PERSONAL_NAME
        signals["personal_name"] = WEIGHT_PERSONAL_NAME

    for addr in addresses:
        local = addr.split("@", 1)[0]
        if _AUTOMATED_LOCAL_RE.search(local):
            score += WEIGHT_AUTOMATED_PATTERN
            signals["automated_pattern"] = WEIGHT_AUTOMATED_PATTERN
            break
    for addr in addresses:
        if _LIST_ADDRESS_RE.search(addr):
            score += WEIGHT_LIST_PATTERN
            signals["list_pattern"] = WEIGHT_LIST_PATTERN
            break

    if messages_from >= 20 and messages_to == 0:
        score += WEIGHT_ONE_WAY_BULK
        signals["one_way_bulk"] = WEIGHT_ONE_WAY_BULK

    probability = max(0.01, min(0.99, score))
    return probability, signals


def classify_contacts(
    pool: ConnectionPool,
    *,
    contact_ids: list[UUID] | None = None,
) -> int:
    """Compute human_probability for contacts. Never writes kind/kind_source.

    With ``contact_ids=None``, classifies the whole corpus. Skips contacts
    whose only address is the user. Returns the number classified.
    """
    with pool.connection() as conn:
        if contact_ids is not None:
            if not contact_ids:
                return 0
            cur = conn.execute(
                """
                SELECT c.id,
                       bool_and(ca.is_user) AS only_user,
                       coalesce(sum(ca.messages_from), 0)::int AS messages_from,
                       coalesce(sum(ca.messages_to), 0)::int AS messages_to,
                       coalesce(
                           array_agg(DISTINCT v) FILTER (WHERE v IS NOT NULL),
                           '{}'::text[]
                       ) AS name_variants,
                       array_agg(DISTINCT ca.address) AS addresses
                  FROM contacts c
                  JOIN contact_addresses ca ON ca.contact_id = c.id
                  LEFT JOIN LATERAL unnest(ca.name_variants) AS v ON TRUE
                 WHERE c.id = ANY(%(ids)s)
                 GROUP BY c.id
                """,
                {"ids": contact_ids},
            )
        else:
            cur = conn.execute(
                """
                SELECT c.id,
                       bool_and(ca.is_user) AS only_user,
                       coalesce(sum(ca.messages_from), 0)::int AS messages_from,
                       coalesce(sum(ca.messages_to), 0)::int AS messages_to,
                       coalesce(
                           array_agg(DISTINCT v) FILTER (WHERE v IS NOT NULL),
                           '{}'::text[]
                       ) AS name_variants,
                       array_agg(DISTINCT ca.address) AS addresses
                  FROM contacts c
                  JOIN contact_addresses ca ON ca.contact_id = c.id
                  LEFT JOIN LATERAL unnest(ca.name_variants) AS v ON TRUE
                 GROUP BY c.id
                """
            )
        rows = cur.fetchall()

        classified = 0
        for (
            contact_id,
            only_user,
            messages_from,
            messages_to,
            name_variants,
            addresses,
        ) in rows:
            if only_user:
                continue
            probability, signals = _compute_probability(
                messages_from=messages_from,
                messages_to=messages_to,
                name_variants=list(name_variants or []),
                addresses=list(addresses or []),
            )
            conn.execute(
                """UPDATE contacts
                      SET human_probability = %(prob)s,
                          classification_signals = %(signals)s::jsonb,
                          classified_at = now(),
                          updated_at = now()
                    WHERE id = %(id)s""",
                {
                    "prob": probability,
                    "signals": json.dumps(signals),
                    "id": contact_id,
                },
            )
            classified += 1
        conn.commit()

    logger.info("contacts_classified", count=classified)
    return classified


def classify_contact(pool: ConnectionPool, contact_id: UUID) -> float:
    """Classify a single contact; return its human_probability."""
    classify_contacts(pool, contact_ids=[contact_id])
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT human_probability FROM contacts WHERE id = %(id)s",
            {"id": contact_id},
        ).fetchone()
    if row is None or row[0] is None:
        return 0.0
    return float(row[0])

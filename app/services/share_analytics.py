# app/services/share_analytics.py
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def _json_safe(value: Any) -> Any:
    """Return a JSON-serialisable value for analytics metadata."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def ensure_share_events_table(db: Session) -> None:
    """Idempotent table creation for local/dev and fresh Railway databases."""
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS share_link_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                link_token TEXT NOT NULL,
                event_type TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_share_link_events_token
            ON share_link_events (link_token)
            """
        )
    )
    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_share_link_events_token_type_created
            ON share_link_events (link_token, event_type, created_at DESC)
            """
        )
    )
    db.commit()


def log_share_event(
    db: Session,
    token: str,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    """
    Record a share analytics event.

    Important: psycopg cannot adapt a raw Python dict through a plain SQL text
    placeholder, so metadata is serialised to JSON text and cast to JSONB.
    """
    if not token or not event_type:
        return

    metadata_json = json.dumps(_json_safe(metadata or {}))

    db.execute(
        text(
            """
            INSERT INTO share_link_events (link_token, event_type, metadata)
            VALUES (:token, :event_type, CAST(:metadata AS JSONB))
            """
        ),
        {
            "token": str(token),
            "event_type": str(event_type),
            "metadata": metadata_json,
        },
    )
    db.commit()

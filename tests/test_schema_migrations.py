"""Schema migrations run at startup and can apply multi-statement SQL files."""

import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi.testclient import TestClient

from app import main as app_main
from app.db.migrate import MIGRATIONS_DIR, ordered_migrations, sql_statements


def test_sql_statements_keep_casts_and_quoted_semicolons():
    statements = sql_statements(
        """
        BEGIN;
        -- comment with a semicolon;
        ALTER TABLE patients
            ADD COLUMN IF NOT EXISTS claimed_by_user_id UUID NULL;
        INSERT INTO app_settings (key, value)
        VALUES ('note', 'keep; this');
        SELECT '{}'::jsonb;
        COMMIT;
        """
    )
    assert len(statements) == 3
    assert "claimed_by_user_id" in statements[0]
    assert "keep; this" in statements[1]
    assert "'{}'::jsonb" in statements[2]


def test_claim_invite_table_precedes_acceptance_without_renaming_migrations():
    filenames = [path.name for path in ordered_migrations()]
    assert sorted(filenames) == sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    assert filenames.index("20261115_patient_claim_invites.sql") < filenames.index(
        "20261115_patient_claim_acceptance.sql"
    )


def test_claim_migration_includes_the_patient_column():
    migration = Path("app/db/migrations/20261115_patient_claim_acceptance.sql").read_text(encoding="utf-8")
    rendered = "\n".join(sql_statements(migration))
    assert "ALTER TABLE patients" in rendered
    assert "claimed_by_user_id" in rendered
    assert "claimed_at" in rendered


def test_lifespan_applies_migrations_before_serving(monkeypatch):
    calls = []
    monkeypatch.setattr(app_main, "run_migrations", lambda engine: calls.append("migrate"))
    monkeypatch.setattr(app_main.Base.metadata, "create_all", lambda bind: calls.append("create"))

    app = app_main.create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert calls == ["migrate", "create"]

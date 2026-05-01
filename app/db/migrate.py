from pathlib import Path
from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(engine):
    with engine.begin() as conn:

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))

        applied = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        for file in files:
            version = file.name

            if version in applied:
                continue

            print(f"[MIGRATION] Applying {version}")

            sql = file.read_text()
            conn.execute(text(sql))

            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version}
            )
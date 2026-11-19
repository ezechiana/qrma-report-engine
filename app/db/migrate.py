from pathlib import Path

from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def sql_statements(sql: str) -> list[str]:
    """Split a migration file into statements the database can execute one at a time.

    psycopg rejects several commands in one execute() call. Quoted text, comments,
    and dollar quotes are left intact. BEGIN and COMMIT are omitted because the
    runner already wraps each file in a transaction.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    length = len(sql)
    in_single = False
    in_double = False
    dollar: str | None = None

    def finish() -> None:
        statement = "".join(buf).strip()
        buf.clear()
        if not statement:
            return
        meaningful = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        ).strip().rstrip(";").strip()
        if meaningful.upper() in {"BEGIN", "COMMIT", "ROLLBACK"}:
            return
        if meaningful:
            statements.append(statement)

    while i < length:
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if dollar is not None:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(char)
            i += 1
            continue

        if in_single:
            buf.append(char)
            if char == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            buf.append(char)
            if char == '"':
                in_double = False
            i += 1
            continue

        if char == "-" and nxt == "-":
            while i < length and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue

        if char == "'":
            in_single = True
            buf.append(char)
            i += 1
            continue

        if char == '"':
            in_double = True
            buf.append(char)
            i += 1
            continue

        if char == "$":
            end = i + 1
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            if end < length and sql[end] == "$":
                dollar = sql[i : end + 1]
                buf.append(dollar)
                i = end + 1
                continue

        if char == ";":
            finish()
            i += 1
            continue

        buf.append(char)
        i += 1

    finish()
    return statements


def ordered_migrations() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    invites = MIGRATIONS_DIR / "20261115_patient_claim_invites.sql"
    acceptance = MIGRATIONS_DIR / "20261115_patient_claim_acceptance.sql"
    if acceptance in files:
        # Acceptance alters the invite table. Preserve both historical ledger names.
        files.remove(invites)
        files.insert(files.index(acceptance), invites)
    return files


def run_migrations(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        applied = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations"))
        }

    for file in ordered_migrations():
        version = file.name
        if version in applied:
            continue

        print(f"[MIGRATION] Applying {version}")
        statements = sql_statements(file.read_text(encoding="utf-8"))
        with engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version},
            )

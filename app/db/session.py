# app/db/session.py

import os
from sqlalchemy import create_engine  # type: ignore[import]
from sqlalchemy.orm import sessionmaker  # type: ignore[import]
from sqlalchemy.engine import url as sa_url  # type: ignore[import]

# --- Get DATABASE_URL from Railway environment ---
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "❌ DATABASE_URL is not set. Please configure it in Railway Variables."
    )

# --- Fix common Railway / Heroku style URL issues ---
# Railway sometimes gives postgres:// instead of postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

# If already correct but missing driver
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# --- DEBUG BLOCK (safe: does NOT print password) ---
try:
    parsed = sa_url.make_url(DATABASE_URL)

    print("\n=== DATABASE DEBUG INFO ===")
    print(f"Driver: {parsed.drivername}")
    print(f"Host: {parsed.host}")
    print(f"Port: {parsed.port}")
    print(f"Database: {parsed.database}")
    print("==========================\n")

except Exception as e:
    print("\n❌ DATABASE URL PARSE ERROR")
    print(f"Raw value: {DATABASE_URL}")
    print(f"Error: {e}\n")


# --- Create engine ---
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

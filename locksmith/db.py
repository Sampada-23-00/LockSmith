"""Embedded Postgres for the demo bank. No install needed: pgserver ships the binaries."""
from pathlib import Path

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parent.parent
PGDATA = ROOT / "pgdata"

NUM_ACCOUNTS = 20
START_BALANCE = 1_000_00  # paise


def server():
    # cleanup_mode=None keeps the server running between scripts
    return pgserver.get_server(PGDATA, cleanup_mode=None)


def uri() -> str:
    return server().get_uri()


def connect() -> psycopg.Connection:
    return psycopg.connect(uri())


def log_dir() -> Path:
    return PGDATA / "log"


def reset_accounts(num_accounts: int = NUM_ACCOUNTS) -> int:
    """Recreate the accounts table and return the total money in the system."""
    with connect() as conn:
        conn.execute("DROP TABLE IF EXISTS accounts")
        conn.execute(
            "CREATE TABLE accounts (id INT PRIMARY KEY, owner TEXT NOT NULL, balance BIGINT NOT NULL CHECK (balance >= 0))"
        )
        conn.execute(
            "INSERT INTO accounts SELECT g, 'user_' || g, %s FROM generate_series(1, %s) g",
            (START_BALANCE, num_accounts),
        )
    return START_BALANCE * num_accounts


def total_balance() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0])

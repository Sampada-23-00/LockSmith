"""Reference fix: let the database enforce one payment per key with a unique index."""
import psycopg

MIGRATIONS: list[str] = [
    "DROP INDEX payments_idempotency_key_idx",
    "CREATE UNIQUE INDEX payments_idempotency_key_idx ON payments (idempotency_key)",
]


def pay(conn: psycopg.Connection, idempotency_key: str, account_id: int, amount: int) -> bool:
    with conn.transaction():
        inserted = conn.execute(
            "INSERT INTO payments (idempotency_key, account_id, amount) VALUES (%s, %s, %s) "
            "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
            (idempotency_key, account_id, amount),
        ).fetchone()
        if inserted is None:
            return False
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))
        return True

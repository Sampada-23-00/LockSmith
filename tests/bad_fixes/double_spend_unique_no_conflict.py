"""Bad fix: a unique index stops double charges, but retries now crash with UniqueViolation.

The client sees an error for a payment that actually went through. The harness must
reject this because operations fail under load.
"""
import psycopg

MIGRATIONS: list[str] = [
    "DROP INDEX payments_idempotency_key_idx",
    "CREATE UNIQUE INDEX payments_idempotency_key_idx ON payments (idempotency_key)",
]


def pay(conn: psycopg.Connection, idempotency_key: str, account_id: int, amount: int) -> bool:
    with conn.transaction():
        seen = conn.execute(
            "SELECT 1 FROM payments WHERE idempotency_key = %s", (idempotency_key,)
        ).fetchone()
        if seen:
            return False
        conn.execute(
            "INSERT INTO payments (idempotency_key, account_id, amount) VALUES (%s, %s, %s)",
            (idempotency_key, account_id, amount),
        )
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))
        return True

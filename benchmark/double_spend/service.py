"""Payment service with idempotency keys.

Clients retry on timeouts, so the same payment request (same idempotency key)
can arrive more than once. Each key must be charged exactly once.

Planted bug (double spend): "has this key been processed?" is checked with a
plain SELECT before inserting. Two retries arriving together both see nothing,
and the customer is charged twice.

The service may define MIGRATIONS, a list of SQL statements applied after
schema.sql when the database is set up.
"""
import psycopg

MIGRATIONS: list[str] = []


def pay(conn: psycopg.Connection, idempotency_key: str, account_id: int, amount: int) -> bool:
    """Charge the account once per key. Returns True if charged, False if the key was already processed."""
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

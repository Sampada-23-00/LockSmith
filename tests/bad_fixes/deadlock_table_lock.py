"""Bad fix: no more deadlocks, but every transfer now waits for every other transfer.

The harness must reject this on performance.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def transfer(conn: psycopg.Connection, from_id: int, to_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("LOCK TABLE accounts IN SHARE ROW EXCLUSIVE MODE")
        sender = conn.execute("SELECT balance FROM accounts WHERE id = %s", (from_id,)).fetchone()
        receiver = conn.execute("SELECT balance FROM accounts WHERE id = %s", (to_id,)).fetchone()

        if sender is None or receiver is None:
            raise ValueError("unknown account")
        if sender[0] < amount:
            raise InsufficientFunds(from_id)

        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, from_id))
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, to_id))

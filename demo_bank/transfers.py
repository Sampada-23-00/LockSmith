"""Demo bank: money transfer service.

This is the "target codebase" LockSmith analyses and repairs.
It contains a planted concurrency bug: rows are locked in request order
(sender first, then receiver), so A->B and B->A running together deadlock.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def transfer(conn: psycopg.Connection, from_id: int, to_id: int, amount: int) -> None:
    with conn.transaction():
        sender = conn.execute(
            "SELECT balance FROM accounts WHERE id = %s FOR UPDATE", (from_id,)
        ).fetchone()
        receiver = conn.execute(
            "SELECT balance FROM accounts WHERE id = %s FOR UPDATE", (to_id,)
        ).fetchone()

        if sender is None or receiver is None:
            raise ValueError("unknown account")
        if sender[0] < amount:
            raise InsufficientFunds(from_id)

        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, from_id))
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, to_id))

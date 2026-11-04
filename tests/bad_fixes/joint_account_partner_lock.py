"""Bad fix: also lock the partner's account, but in request order (own first, then partner).

Partners withdrawing at the same time now lock each other's rows in opposite orders.
The harness must reject this because of deadlocks under load.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        row = conn.execute("SELECT household, balance FROM accounts WHERE id = %s FOR UPDATE", (account_id,)).fetchone()
        if row is None:
            raise ValueError("unknown account")
        partner = conn.execute(
            "SELECT balance FROM accounts WHERE household = %s AND id <> %s FOR UPDATE", (row[0], account_id)
        ).fetchone()
        if row[1] + (partner[0] if partner else 0) < amount:
            raise InsufficientFunds(account_id)
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))

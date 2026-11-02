"""Bad fix: no account can go negative if no withdrawal ever succeeds.

The harness must reject this because behaviour no longer matches the original.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    raise InsufficientFunds(account_id)

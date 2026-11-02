"""Wallet service: top-ups and spends on a customer's wallet.

Planted bug (lost update): the balance is read into Python, changed there, and
written back as an absolute value. Two requests that read the same old balance
both write, and one of the changes silently disappears.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def _read_balance(conn: psycopg.Connection, account_id: int) -> int:
    row = conn.execute("SELECT balance FROM accounts WHERE id = %s", (account_id,)).fetchone()
    if row is None:
        raise ValueError("unknown account")
    return row[0]


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        balance = _read_balance(conn, account_id)
        conn.execute("UPDATE accounts SET balance = %s WHERE id = %s", (balance + amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        balance = _read_balance(conn, account_id)
        if balance < amount:
            raise InsufficientFunds(account_id)
        conn.execute("UPDATE accounts SET balance = %s WHERE id = %s", (balance - amount, account_id))

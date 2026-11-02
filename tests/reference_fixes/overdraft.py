"""Reference fix: make check-and-debit a single conditional UPDATE."""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        debited = conn.execute(
            "UPDATE accounts SET balance = balance - %s WHERE id = %s AND balance >= %s RETURNING id",
            (amount, account_id, amount),
        ).fetchone()
        if debited is None:
            if conn.execute("SELECT 1 FROM accounts WHERE id = %s", (account_id,)).fetchone() is None:
                raise ValueError("unknown account")
            raise InsufficientFunds(account_id)

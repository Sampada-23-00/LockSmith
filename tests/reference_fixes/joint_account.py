"""Reference fix: lock every account in the household, in id order, before checking the total."""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, balance FROM accounts "
            "WHERE household = (SELECT household FROM accounts WHERE id = %s) ORDER BY id FOR UPDATE",
            (account_id,),
        ).fetchall()
        if not rows:
            raise ValueError("unknown account")
        if sum(balance for _, balance in rows) < amount:
            raise InsufficientFunds(account_id)
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))

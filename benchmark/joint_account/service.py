"""Household accounts with a shared overdraft rule.

Partners in a household each have their own account. One partner may go below
zero as long as the household's combined balance stays at or above zero.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        row = conn.execute(
            "SELECT household FROM accounts WHERE id = %s FOR UPDATE", (account_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown account")
        (combined,) = conn.execute(
            "SELECT SUM(balance) FROM accounts WHERE household = %s", (row[0],)
        ).fetchone()
        if combined < amount:
            raise InsufficientFunds(account_id)
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))

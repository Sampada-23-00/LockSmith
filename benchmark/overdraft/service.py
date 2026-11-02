"""Withdrawal service for accounts without an overdraft facility.

Planted bug (overdraft race): the balance check and the debit are separate steps
with no lock in between. Two withdrawals can both pass the check against the
same balance, and the account goes negative.
"""
import psycopg


class InsufficientFunds(Exception):
    pass


def deposit(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, account_id))


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        row = conn.execute("SELECT balance FROM accounts WHERE id = %s", (account_id,)).fetchone()
        if row is None:
            raise ValueError("unknown account")
        if row[0] < amount:
            raise InsufficientFunds(account_id)
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))

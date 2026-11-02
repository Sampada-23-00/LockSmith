"""Reference fix: lock both rows in one statement, in id order."""
import psycopg


class InsufficientFunds(Exception):
    pass


def transfer(conn: psycopg.Connection, from_id: int, to_id: int, amount: int) -> None:
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, balance FROM accounts WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            ([from_id, to_id],),
        ).fetchall()
        balances = dict(rows)

        if from_id not in balances or to_id not in balances:
            raise ValueError("unknown account")
        if balances[from_id] < amount:
            raise InsufficientFunds(from_id)

        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, from_id))
        conn.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, to_id))

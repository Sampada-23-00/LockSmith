"""Bad fix: lock today's existing withdrawal rows with FOR UPDATE, then sum them.

Row locks cannot lock rows that do not exist yet, so two requests still both see the
same total and both insert. The harness must reject this because the limit is exceeded.
"""
import psycopg

DAILY_LIMIT = 5_000

MIGRATIONS: list[str] = []


class DailyLimitExceeded(Exception):
    pass


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        rows = conn.execute(
            "SELECT amount FROM withdrawals WHERE account_id = %s AND day = current_date FOR UPDATE",
            (account_id,),
        ).fetchall()
        if sum(a for (a,) in rows) + amount > DAILY_LIMIT:
            raise DailyLimitExceeded(account_id)
        conn.execute("INSERT INTO withdrawals (account_id, amount) VALUES (%s, %s)", (account_id, amount))
        updated = conn.execute(
            "UPDATE accounts SET balance = balance - %s WHERE id = %s RETURNING id", (amount, account_id)
        ).fetchone()
        if updated is None:
            raise ValueError("unknown account")

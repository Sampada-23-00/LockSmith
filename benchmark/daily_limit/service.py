"""ATM / UPI withdrawals with a per-account daily limit.

Each account may withdraw at most DAILY_LIMIT paise per calendar day.
"""
import psycopg

DAILY_LIMIT = 5_000  # ₹50 per day

MIGRATIONS: list[str] = []


class DailyLimitExceeded(Exception):
    pass


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        (used,) = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE account_id = %s AND day = current_date",
            (account_id,),
        ).fetchone()
        if used + amount > DAILY_LIMIT:
            raise DailyLimitExceeded(account_id)
        conn.execute("INSERT INTO withdrawals (account_id, amount) VALUES (%s, %s)", (account_id, amount))
        updated = conn.execute(
            "UPDATE accounts SET balance = balance - %s WHERE id = %s RETURNING id", (amount, account_id)
        ).fetchone()
        if updated is None:
            raise ValueError("unknown account")

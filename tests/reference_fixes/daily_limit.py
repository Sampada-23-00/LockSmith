"""Reference fix: serialise each account's withdrawals by locking the account row first."""
import psycopg

DAILY_LIMIT = 5_000

MIGRATIONS: list[str] = []


class DailyLimitExceeded(Exception):
    pass


def withdraw(conn: psycopg.Connection, account_id: int, amount: int) -> None:
    with conn.transaction():
        if conn.execute("SELECT 1 FROM accounts WHERE id = %s FOR UPDATE", (account_id,)).fetchone() is None:
            raise ValueError("unknown account")
        (used,) = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE account_id = %s AND day = current_date",
            (account_id,),
        ).fetchone()
        if used + amount > DAILY_LIMIT:
            raise DailyLimitExceeded(account_id)
        conn.execute("INSERT INTO withdrawals (account_id, amount) VALUES (%s, %s)", (account_id, amount))
        conn.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, account_id))

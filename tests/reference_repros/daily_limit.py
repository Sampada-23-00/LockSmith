"""Three ₹30 withdrawals from one account at the same instant, against a ₹50 daily limit."""
import threading


def reproduce(connect, service):
    conns = [connect() for _ in range(3)]
    barrier = threading.Barrier(3)

    def withdraw(conn):
        barrier.wait()
        try:
            service.withdraw(conn, 1, 3_000)
        except Exception:
            pass  # a rejected withdrawal is fine

    threads = [threading.Thread(target=withdraw, args=(c,)) for c in conns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()

    with connect() as conn:
        (total,) = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE account_id = 1 AND day = current_date"
        ).fetchone()
    return f"account 1 withdrew {total} today against a limit of {service.DAILY_LIMIT}" if total > service.DAILY_LIMIT else None

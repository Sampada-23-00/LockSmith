"""Two withdrawals of ₹8 from a ₹10 account, released at the same instant."""
import threading


def reproduce(connect, service):
    conns = [connect() for _ in range(2)]
    barrier = threading.Barrier(2)

    def withdraw(conn):
        barrier.wait()
        try:
            service.withdraw(conn, 1, 800)
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
        balance = conn.execute("SELECT balance FROM accounts WHERE id = 1").fetchone()[0]
    return f"account 1 overdrawn to {balance}" if balance < 0 else None

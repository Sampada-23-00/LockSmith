"""Both partners of household 1 (₹10 each) withdraw ₹15 at the same instant."""
import threading


def reproduce(connect, service):
    conns = [connect() for _ in range(2)]
    barrier = threading.Barrier(2)

    def withdraw(conn, account_id):
        barrier.wait()
        try:
            service.withdraw(conn, account_id, 1_500)
        except Exception:
            pass  # a rejected withdrawal is fine

    threads = [threading.Thread(target=withdraw, args=(conns[i], i + 1)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()

    with connect() as conn:
        (total,) = conn.execute("SELECT SUM(balance) FROM accounts WHERE id IN (1, 2)").fetchone()
    return f"household 1 combined balance is {total}" if total < 0 else None

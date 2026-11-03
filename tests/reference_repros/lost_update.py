"""Two ₹1 deposits to the same wallet at the same instant: the balance must rise by ₹2."""
import threading


def reproduce(connect, service):
    with connect() as conn:
        before = conn.execute("SELECT balance FROM accounts WHERE id = 1").fetchone()[0]

    conns = [connect() for _ in range(2)]
    barrier = threading.Barrier(2)

    def deposit(conn):
        barrier.wait()
        service.deposit(conn, 1, 100)

    threads = [threading.Thread(target=deposit, args=(c,)) for c in conns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()

    with connect() as conn:
        after = conn.execute("SELECT balance FROM accounts WHERE id = 1").fetchone()[0]
    return f"two deposits of 100 moved the balance by {after - before}" if after - before != 200 else None

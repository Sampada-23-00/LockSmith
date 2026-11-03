"""The same payment request arrives three times at once (client retries)."""
import threading


def reproduce(connect, service):
    conns = [connect() for _ in range(3)]
    barrier = threading.Barrier(3)

    def pay(conn):
        barrier.wait()
        service.pay(conn, "order-42", 1, 500)

    threads = [threading.Thread(target=pay, args=(c,)) for c in conns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()

    with connect() as conn:
        charges = conn.execute("SELECT COUNT(*) FROM payments WHERE idempotency_key = 'order-42'").fetchone()[0]
    return f"payment order-42 charged {charges} times" if charges != 1 else None

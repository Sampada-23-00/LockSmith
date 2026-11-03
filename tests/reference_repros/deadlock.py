"""Transfers 1->2 and 2->1 started at the same instant."""
import threading

from psycopg import errors


def reproduce(connect, service):
    conns = [connect() for _ in range(2)]
    barrier = threading.Barrier(2)
    deadlocks = []

    def transfer(conn, a, b):
        barrier.wait()
        try:
            service.transfer(conn, a, b, 100)
        except errors.DeadlockDetected:
            deadlocks.append((a, b))

    threads = [
        threading.Thread(target=transfer, args=(conns[0], 1, 2)),
        threading.Thread(target=transfer, args=(conns[1], 2, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in conns:
        c.close()
    return f"transfer {deadlocks[0]} was killed by a deadlock" if deadlocks else None

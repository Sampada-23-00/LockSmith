"""Concurrency stress test: hammer a transfer() implementation and report what broke.

Usage: python -m locksmith.stress [path/to/transfers.py] [--workers N] [--ops N]
"""
import argparse
import importlib.util
import json
import random
import threading
import time
from pathlib import Path

from psycopg import errors

from locksmith import db

DEFAULT_TARGET = db.ROOT / "demo_bank" / "transfers.py"


def load_transfer(path: Path):
    spec = importlib.util.spec_from_file_location(f"target_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.transfer


def run(target: Path = DEFAULT_TARGET, workers: int = 16, ops: int = 50, hot_accounts: int = 4, seed: int = 7) -> dict:
    transfer = load_transfer(Path(target))
    expected_total = db.reset_accounts()
    uri = db.uri()

    counts = {"ok": 0, "deadlocks": 0, "insufficient_funds": 0, "other_errors": 0}
    error_samples: list[str] = []
    lock = threading.Lock()

    def worker(worker_id: int):
        import psycopg

        rng = random.Random(seed + worker_id)
        with psycopg.connect(uri, autocommit=True) as conn:
            # Detect deadlocks fast so the test finishes quickly (default is 1s)
            conn.execute("SET deadlock_timeout = '50ms'")
            for _ in range(ops):
                # Small set of hot accounts so opposite-direction transfers collide
                a, b = rng.sample(range(1, hot_accounts + 1), 2)
                try:
                    transfer(conn, a, b, rng.randint(1, 500))
                    key = "ok"
                except errors.DeadlockDetected:
                    key = "deadlocks"
                except Exception as exc:
                    if type(exc).__name__ == "InsufficientFunds":
                        key = "insufficient_funds"
                    else:
                        key = "other_errors"
                        with lock:
                            if len(error_samples) < 5:
                                error_samples.append(f"{type(exc).__name__}: {exc}")
                with lock:
                    counts[key] += 1

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started

    actual_total = db.total_balance()
    return {
        "target": str(target),
        "attempted": workers * ops,
        **counts,
        "error_samples": error_samples,
        "money_conserved": actual_total == expected_total,
        "expected_total": expected_total,
        "actual_total": actual_total,
        "seconds": round(elapsed, 2),
        "passed": counts["deadlocks"] == 0 and counts["other_errors"] == 0 and actual_total == expected_total,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default=str(DEFAULT_TARGET))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--ops", type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.target), args.workers, args.ops), indent=2))


if __name__ == "__main__":
    main()

"""Independent verification harness. The agent can ask for a verdict but never produce one.

A candidate fix is accepted only if all of these hold:
  1. Same behaviour: run one request at a time, it returns exactly what the original
     returns and leaves the database in exactly the same state.
  2. Safe under load: on every seed of a contended workload there are no deadlocks,
     no unexpected errors, no balance ever negative (audited on every write), and the
     money checks pass.
  3. Not slower: on a low-contention workload its throughput is at least
     MIN_THROUGHPUT_RATIO of the original's (catches "lock the whole table" fixes).

Usage:
  python -m locksmith.harness baseline [scenario ...]
  python -m locksmith.harness verify <scenario> <candidate.py>
"""
import argparse
import importlib.util
import itertools
import json
import os
import statistics
import threading
import time
from pathlib import Path

import psycopg
from psycopg import errors

from locksmith import db
from locksmith.scenarios import SCENARIOS, Plan, Scenario, balances

WORKERS = 16
OPS_PER_WORKER = 30
PRACTICE_SEEDS = (1, 2, 3)  # the agent may test against these as often as it likes
HOLDOUT_SEEDS = (101, 102, 103)  # used only for the final verdict, never shown to the agent
MIN_THROUGHPUT_RATIO = float(os.environ.get("LOCKSMITH_MIN_THROUGHPUT_RATIO", "0.5"))
# The database runs on this machine, so queries take microseconds. Production has a network
# between app and database; without it, holding locks across queries looks free.
NETWORK_LATENCY_S = float(os.environ.get("LOCKSMITH_NETWORK_LATENCY_MS", "2")) / 1000
MAX_LISTED_PROBLEMS = 5

_module_counter = itertools.count()


class NetworkConnection:
    """A connection whose queries each pay one simulated app-to-database round trip."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def execute(self, *args, **kwargs):
        time.sleep(NETWORK_LATENCY_S)
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def load_service(path: Path):
    spec = importlib.util.spec_from_file_location(f"service_{next(_module_counter)}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Records every committed moment a balance goes negative, even if a later deposit repairs it.
# Installed by the harness after the service's own setup, so the service never sees it.
AUDIT_SQL = """
DROP TABLE IF EXISTS locksmith_audit;
CREATE TABLE locksmith_audit (account_id INT, balance BIGINT);
CREATE OR REPLACE FUNCTION locksmith_audit_negative() RETURNS trigger AS $$
BEGIN
    INSERT INTO locksmith_audit VALUES (NEW.id, NEW.balance);
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER locksmith_audit_negative AFTER INSERT OR UPDATE ON accounts
    FOR EACH ROW WHEN (NEW.balance < 0) EXECUTE FUNCTION locksmith_audit_negative();
"""


def reset_database(scenario: Scenario, service) -> dict[int, int]:
    with psycopg.connect(db.uri(), autocommit=True) as conn:
        conn.execute(scenario.schema_sql)
        conn.execute(
            "INSERT INTO accounts SELECT g, 'user_' || g, %s FROM generate_series(1, %s) g",
            (scenario.start_balance, scenario.num_accounts),
        )
        for statement in getattr(service, "MIGRATIONS", []):
            conn.execute(statement)
        conn.execute(AUDIT_SQL)
        return balances(conn)


def snapshot() -> dict:
    with psycopg.connect(db.uri()) as conn:
        state = {"balances": balances(conn)}
        if conn.execute("SELECT to_regclass('payments')").fetchone()[0]:
            state["payments"] = conn.execute(
                "SELECT idempotency_key, account_id, amount FROM payments ORDER BY idempotency_key, id"
            ).fetchall()
        return state


def run_workload(scenario: Scenario, service_path: Path, plan: Plan) -> dict:
    service = load_service(service_path)
    start = reset_database(scenario, service)
    uri = db.uri()
    results: list[dict] = []
    lock = threading.Lock()

    def worker(ops):
        with psycopg.connect(uri, autocommit=True) as raw:
            raw.execute("SET deadlock_timeout = '50ms'")  # detect deadlocks fast instead of after 1s
            conn = NetworkConnection(raw)
            for fn, kwargs in ops:
                value = None
                t0 = time.perf_counter()
                try:
                    value = getattr(service, fn)(conn, **kwargs)
                    outcome = "ok"
                except errors.DeadlockDetected:
                    outcome = "deadlock"
                except Exception as exc:
                    if type(exc).__name__ in scenario.business_errors:
                        outcome = "rejected"
                    else:
                        outcome = "error"
                        value = f"{type(exc).__name__}: {exc}".strip()
                latency = time.perf_counter() - t0
                with lock:
                    results.append({"fn": fn, "kwargs": kwargs, "outcome": outcome, "value": value, "latency": latency})

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(ops,)) for ops in plan]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seconds = time.perf_counter() - started

    counts = {k: sum(r["outcome"] == k for r in results) for k in ("ok", "rejected", "deadlock", "error")}
    problems = []
    if counts["deadlock"]:
        problems.append(f"{counts['deadlock']} operations failed with deadlock detected")
    if counts["error"]:
        samples = sorted({r["value"] for r in results if r["outcome"] == "error"})[:3]
        problems.append(f"{counts['error']} operations raised unexpected errors, e.g. {samples}")
    with psycopg.connect(uri) as conn:
        negative = conn.execute(
            "SELECT account_id, COUNT(*), MIN(balance) FROM locksmith_audit GROUP BY 1 ORDER BY 1"
        ).fetchall()
        problems += [f"account {a} went negative {n} times (lowest balance {low})" for a, n, low in negative]
        problems += scenario.check(conn, results, start)

    latencies = sorted(r["latency"] for r in results)
    return {
        "operations": len(results),
        **counts,
        "problems": problems,
        "seconds": round(seconds, 3),
        "throughput_ops_per_s": round(len(results) / seconds, 1),
        "p95_latency_ms": round(latencies[int(0.95 * (len(latencies) - 1))] * 1000, 2) if latencies else None,
        "_results": results,
    }


def _summary(run: dict) -> dict:
    out = {k: v for k, v in run.items() if not k.startswith("_")}
    if len(out["problems"]) > MAX_LISTED_PROBLEMS:
        out["problems"] = out["problems"][:MAX_LISTED_PROBLEMS] + [f"... and {len(run['problems']) - MAX_LISTED_PROBLEMS} more"]
    return out


def _serial_plan(scenario: Scenario, seed: int) -> Plan:
    # Interleave several workers' operations into one sequence, run by a single worker
    per_worker = scenario.contended(seed, 4, 10)
    return [[op for group in zip(*per_worker) for op in group]]


def check_same_behaviour(scenario: Scenario, candidate: Path, seed: int) -> dict:
    plan = _serial_plan(scenario, seed)
    original = run_workload(scenario, scenario.service_path, plan)
    original_state = snapshot()
    fixed = run_workload(scenario, candidate, plan)
    fixed_state = snapshot()

    differences = []
    for i, (a, b) in enumerate(zip(original["_results"], fixed["_results"])):
        if (a["outcome"], a["value"]) != (b["outcome"], b["value"]):
            differences.append(
                f"request {i} {a['fn']}({a['kwargs']}): original -> {a['outcome']} {a['value']!r}, "
                f"fix -> {b['outcome']} {b['value']!r}"
            )
    if original_state != fixed_state:
        differences.append("final database state differs from the original's")
    return {
        "name": "same_behaviour",
        "passed": not differences,
        "detail": differences[:MAX_LISTED_PROBLEMS] or f"{len(plan[0])} sequential requests matched the original exactly",
    }


def check_under_load(scenario: Scenario, candidate: Path, seed: int) -> dict:
    run = run_workload(scenario, candidate, scenario.contended(seed, WORKERS, OPS_PER_WORKER))
    summary = _summary(run)
    return {"name": f"under_load_seed_{seed}", "passed": not run["problems"], "detail": summary}


def check_performance(scenario: Scenario, candidate: Path, seed: int) -> dict:
    plan = scenario.calm(seed, WORKERS, OPS_PER_WORKER)
    original = run_workload(scenario, scenario.service_path, plan)
    fixed = run_workload(scenario, candidate, plan)
    ratio = fixed["throughput_ops_per_s"] / original["throughput_ops_per_s"]
    return {
        "name": "performance",
        "passed": ratio >= MIN_THROUGHPUT_RATIO,
        "detail": {
            "original_ops_per_s": original["throughput_ops_per_s"],
            "fix_ops_per_s": fixed["throughput_ops_per_s"],
            "ratio": round(ratio, 2),
            "required_ratio": MIN_THROUGHPUT_RATIO,
            "original_p95_ms": original["p95_latency_ms"],
            "fix_p95_ms": fixed["p95_latency_ms"],
        },
    }


def verify(scenario_name: str, candidate: Path, seeds=PRACTICE_SEEDS) -> dict:
    scenario = SCENARIOS[scenario_name]
    candidate = Path(candidate)
    started = time.perf_counter()
    checks = []
    try:
        load_service(candidate)
    except Exception as exc:
        checks.append({"name": "loads", "passed": False, "detail": f"{type(exc).__name__}: {exc}"})
    else:
        checks.append(check_same_behaviour(scenario, candidate, seeds[0]))
        checks += [check_under_load(scenario, candidate, s) for s in seeds]
        checks.append(check_performance(scenario, candidate, seeds[0]))
    return {
        "scenario": scenario_name,
        "candidate": str(candidate),
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "seconds": round(time.perf_counter() - started, 1),
    }


def baseline(scenario_name: str, seed: int = PRACTICE_SEEDS[0]) -> dict:
    """Run the original (buggy) service under load: evidence that the bug is real."""
    scenario = SCENARIOS[scenario_name]
    run = run_workload(scenario, scenario.service_path, scenario.contended(seed, WORKERS, OPS_PER_WORKER))
    return {"scenario": scenario_name, "bug_reproduced": bool(run["problems"]), **_summary(run)}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    v = sub.add_parser("verify")
    v.add_argument("scenario", choices=list(SCENARIOS))
    v.add_argument("candidate")
    v.add_argument("--holdout", action="store_true", help="use the held-out seeds")
    args = parser.parse_args()

    if args.command == "baseline":
        for name in args.scenarios:
            print(json.dumps(baseline(name), indent=2, default=str))
    else:
        seeds = HOLDOUT_SEEDS if args.holdout else PRACTICE_SEEDS
        print(json.dumps(verify(args.scenario, Path(args.candidate), seeds), indent=2, default=str))


if __name__ == "__main__":
    main()

"""Run reproduction tests written by the agent, in a separate process with a timeout.

A reproduction test is a Python file defining

    def reproduce(connect, service) -> str | None

`connect()` opens a new database connection (autocommit, simulated network latency;
usable as a context manager) and `service` is the service module under test. The
database is reset before every call. The test returns a description of the money rule
that broke, or None if everything held.

A test is only trusted if it catches the bug in the original code on every run and
never reports a problem in a known-correct implementation: a test that always fails
proves nothing.

Usage (internal): python -m locksmith.repro <scenario> <service.py> <repro.py> <runs>
"""
import json
import subprocess
import sys
from pathlib import Path

import psycopg

from locksmith import db
from locksmith.harness import NetworkConnection, load_service, reset_database
from locksmith.scenarios import SCENARIOS

RUNS = 3
TIMEOUT_S = 120


def _connect() -> NetworkConnection:
    raw = psycopg.connect(db.uri(), autocommit=True)
    raw.execute("SET deadlock_timeout = '50ms'")
    return NetworkConnection(raw)


def _run_here(scenario_name: str, service_path: Path, repro_path: Path, runs: int) -> list[dict]:
    scenario = SCENARIOS[scenario_name]
    results = []
    for _ in range(runs):
        service = load_service(service_path)
        reset_database(scenario, service)
        try:
            violation = load_service(repro_path).reproduce(_connect, service)
            results.append({"violation": str(violation) if violation else None})
        except Exception as exc:
            results.append({"violation": None, "error": f"{type(exc).__name__}: {exc}"})
    return results


def run(scenario_name: str, service_path: Path, repro_path: Path, runs: int = RUNS) -> list[dict]:
    """Run the test against a service in a child process, so a hung test can be killed."""
    command = [sys.executable, "-m", "locksmith.repro", scenario_name, str(service_path), str(repro_path), str(runs)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=db.ROOT)
    except subprocess.TimeoutExpired:
        return [{"violation": None, "error": f"test did not finish within {TIMEOUT_S}s (hung threads or locks?)"}]
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return [{"violation": None, "error": (proc.stderr.strip() or "no output")[-1500:]}]
    return json.loads(lines[-1])


def evaluate(scenario_name: str, repro_path: Path) -> dict:
    """Is this a trustworthy reproduction of the scenario's bug?"""
    scenario = SCENARIOS[scenario_name]
    on_original = run(scenario_name, scenario.service_path, Path(repro_path))
    on_correct = run(scenario_name, scenario.reference_path, Path(repro_path))

    detected = sum(bool(r["violation"]) for r in on_original)
    false_alarms = sum(bool(r["violation"]) for r in on_correct)
    errors = [r["error"] for r in on_original + on_correct if r.get("error")]
    report = {
        "detects_bug_in_original_code": f"{detected}/{len(on_original)}",
        "false_alarms_on_a_correct_implementation": f"{false_alarms}/{len(on_correct)}",
        "example_violation": next((r["violation"] for r in on_original if r["violation"]), None),
        "errors": errors[:3],
    }
    report["valid"] = detected == len(on_original) and false_alarms == 0 and not errors
    if not report["valid"]:
        report["why_not_valid"] = (
            "the test must report a violation on every run against the original code, "
            "never report one against a correct implementation, and never crash"
        )
    return report


def check_candidate(scenario_name: str, candidate: Path, repro_path: Path) -> dict:
    results = run(scenario_name, Path(candidate), Path(repro_path))
    problems = [r.get("error") or r["violation"] for r in results if r["violation"] or r.get("error")]
    return {
        "name": "agent_reproduction_test",
        "passed": not problems,
        "detail": problems[:3] or f"the agent's own reproduction test passed {len(results)}/{len(results)} runs",
    }


if __name__ == "__main__":
    scenario_name, service, repro, runs = sys.argv[1:5]
    results = _run_here(scenario_name, Path(service), Path(repro), int(runs))
    print(json.dumps(results))

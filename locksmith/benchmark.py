"""Run the agent on every scenario and record what happened, for the README and dashboard.

Usage: python -m locksmith.benchmark [scenario ...]
"""
import json
import sys
import time
from datetime import datetime

import openai

from locksmith import agent, budget, db
from locksmith.scenarios import SCENARIOS

RESULTS_DIR = db.ROOT / "results"


def _relative_paths(text: str) -> str:
    """Results are published, so drop this machine's directory names from them."""
    root = json.dumps(str(db.ROOT))[1:-1]  # as it appears inside JSON strings
    return text.replace(root + "\\\\", "").replace(root, ".")


def run_one(name: str) -> dict:
    events = []

    def on_event(event):
        events.append(event)
        print(json.dumps(event, default=str)[:300], flush=True)

    spent_before = budget.spent()["total_usd"]
    started = time.perf_counter()
    try:
        session = agent.run(name, on_event)
        aborted = None
    except (agent.InfrastructureError, openai.APIError, budget.BudgetExceeded) as exc:
        session, aborted = None, f"{type(exc).__name__}: {exc}"

    tool_calls = [e["name"] for e in events if e["type"] == "tool_call"]
    record = {
        "scenario": name,
        "title": SCENARIOS[name].title,
        "model": agent.MODEL,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "accepted": bool(session and session.accepted),
        "aborted": aborted,
        "repro_attempts": tool_calls.count("write_repro"),
        "fix_attempts": tool_calls.count("write_candidate"),
        "verification_runs": tool_calls.count("run_verification"),
        "submissions": tool_calls.count("submit_fix"),
        "seconds": round(time.perf_counter() - started, 1),
        "cost_usd": round(budget.spent()["total_usd"] - spent_before, 4),
    }
    if session and session.accepted:
        accepted = session.accepted
        record.update(
            root_cause=accepted["root_cause"],
            fix=accepted["fix"],
            repro_report=accepted["repro_report"],
            verdict=accepted["verdict"],
            repro_code=open(accepted["repro_path"], encoding="utf-8").read(),
            fix_code=open(accepted["path"], encoding="utf-8").read(),
        )
    (RESULTS_DIR / f"{name}.json").write_text(
        _relative_paths(json.dumps(record, indent=2, default=str)), encoding="utf-8"
    )
    (RESULTS_DIR / f"{name}.events.jsonl").write_text(
        _relative_paths("\n".join(json.dumps(e, default=str) for e in events)), encoding="utf-8"
    )
    return record


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    for name in sys.argv[1:] or list(SCENARIOS):
        r = run_one(name)
        print(
            f"\n== {name}: accepted={r['accepted']} repro_attempts={r['repro_attempts']} "
            f"fix_attempts={r['fix_attempts']} {r['seconds']}s ${r['cost_usd']}\n",
            flush=True,
        )


if __name__ == "__main__":
    main()

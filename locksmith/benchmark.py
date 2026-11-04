"""Run the agent on every scenario and record what happened, for the README and dashboard.

Usage: python -m locksmith.benchmark [scenario ...] [--tag run2]

The primary model's untagged run goes to results/ (what the dashboard shows). Other
models and tagged repeat runs go to results/models/<model>/[<tag>/].
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import openai

from locksmith import agent, budget, db
from locksmith.scenarios import SCENARIOS

RESULTS_DIR = db.ROOT / "results"
PRIMARY_MODEL = "muse-spark-1.3"  # its results live in results/; other models get their own folder


def output_dir(tag: str | None = None) -> Path:
    if agent.MODEL == PRIMARY_MODEL and tag is None:
        out = RESULTS_DIR
    else:
        out = RESULTS_DIR / "models" / agent.MODEL / (tag or "")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _relative_paths(text: str) -> str:
    """Results are published, so drop this machine's directory names from them."""
    root = json.dumps(str(db.ROOT))[1:-1]  # as it appears inside JSON strings
    return text.replace(root + "\\\\", "").replace(root, ".")


def run_one(name: str, show=None, tag: str | None = None) -> dict:
    """Run the agent on one scenario and save the record. `show` also receives each event."""
    events = []

    def on_event(event):
        events.append(event)
        print(json.dumps(event, default=str)[:300], flush=True)
        if show:
            show(event)

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
    out = output_dir(tag)
    (out / f"{name}.json").write_text(
        _relative_paths(json.dumps(record, indent=2, default=str)), encoding="utf-8"
    )
    (out / f"{name}.events.jsonl").write_text(
        _relative_paths("\n".join(json.dumps(e, default=str) for e in events)), encoding="utf-8"
    )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="*", help=f"default: all of {', '.join(SCENARIOS)}")
    parser.add_argument("--tag", help="label for a repeat run, e.g. run2")
    args = parser.parse_args()
    unknown = set(args.scenarios) - set(SCENARIOS)
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
    for name in args.scenarios or list(SCENARIOS):
        existing = output_dir(args.tag) / f"{name}.json"
        if args.tag and existing.exists():
            print(f"== {name}: already recorded for {args.tag}, skipping", flush=True)
            continue
        r = run_one(name, tag=args.tag)
        print(
            f"\n== {name}: accepted={r['accepted']} repro_attempts={r['repro_attempts']} "
            f"fix_attempts={r['fix_attempts']} {r['seconds']}s ${r['cost_usd']}\n",
            flush=True,
        )


if __name__ == "__main__":
    main()

"""Combine every recorded agent run into success rates per model and scenario.

Usage: python -m locksmith.summary   (writes results/summary.json and prints a Markdown table)
"""
import json
import statistics
from collections import defaultdict

from locksmith.benchmark import PRIMARY_MODEL, RESULTS_DIR
from locksmith.scenarios import SCENARIOS


def load_runs() -> list[dict]:
    paths = list(RESULTS_DIR.glob("*.json")) + list((RESULTS_DIR / "models").rglob("*.json"))
    runs = []
    for path in paths:
        if path.name == "summary.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if "scenario" in record:
            record.setdefault("model", PRIMARY_MODEL)
            runs.append(record)
    return runs


def summarize(runs: list[dict]) -> dict:
    by_key = defaultdict(list)
    for r in runs:
        by_key[(r["model"], r["scenario"])].append(r)

    rows = []
    for (model, scenario), group in sorted(by_key.items(), key=lambda kv: (kv[0][0], list(SCENARIOS).index(kv[0][1]))):
        accepted = [r for r in group if r["accepted"]]
        rows.append(
            {
                "model": model,
                "scenario": scenario,
                "runs": len(group),
                "accepted": len(accepted),
                "mean_repro_attempts": round(statistics.mean(r["repro_attempts"] for r in group), 2),
                "mean_fix_attempts": round(statistics.mean(r["fix_attempts"] for r in group), 2),
                "mean_minutes": round(statistics.mean(r["seconds"] for r in group) / 60, 1),
                "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in group), 3),
            }
        )

    models = {}
    for model in sorted({r["model"] for r in runs}):
        group = [r for r in runs if r["model"] == model]
        models[model] = {
            "runs": len(group),
            "accepted": sum(r["accepted"] for r in group),
            "first_try_fixes": sum(r["accepted"] and r["fix_attempts"] == 1 for r in group),
            "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in group), 3),
            "total_cost_usd": round(sum(r["cost_usd"] for r in group), 2),
            "mean_minutes": round(statistics.mean(r["seconds"] for r in group) / 60, 1),
        }
    return {"models": models, "rows": rows}


def markdown(summary: dict) -> str:
    lines = [
        "| Model | Runs | Accepted | Fixed on 1st attempt | Mean cost / bug | Mean time / bug |",
        "|---|---|---|---|---|---|",
    ]
    for model, m in summary["models"].items():
        lines.append(
            f"| {model} | {m['runs']} | {m['accepted']}/{m['runs']} | {m['first_try_fixes']}/{m['runs']} "
            f"| ${m['mean_cost_usd']:.3f} | {m['mean_minutes']} min |"
        )
    lines += ["", "| Model | Bug | Accepted | Mean repro tests | Mean fixes | Mean cost |", "|---|---|---|---|---|---|"]
    for r in summary["rows"]:
        lines.append(
            f"| {r['model']} | {r['scenario']} | {r['accepted']}/{r['runs']} | {r['mean_repro_attempts']} "
            f"| {r['mean_fix_attempts']} | ${r['mean_cost_usd']:.3f} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    summary = summarize(load_runs())
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(markdown(summary))

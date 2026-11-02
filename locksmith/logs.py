"""Parse Postgres server logs into structured deadlock events."""
import re
from pathlib import Path

from locksmith import db

_PROCESS_SQL = re.compile(r"^\s*Process (\d+): (.+)$")
_WAITS = re.compile(r"Process (\d+) waits for (.+?); blocked by process (\d+)")


def deadlock_events(log_path: Path | None = None) -> list[dict]:
    lines = (log_path or db.log_dir()).read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    i = 0
    while i < len(lines):
        if "ERROR:  deadlock detected" not in lines[i]:
            i += 1
            continue
        event = {"timestamp": lines[i][:23], "waits": [], "statements": {}}
        i += 1
        # The event continues on DETAIL/HINT/CONTEXT/STATEMENT lines and tab-indented continuations
        while i < len(lines) and (lines[i].startswith("\t") or re.search(r"\] (DETAIL|HINT|CONTEXT|STATEMENT):", lines[i])):
            line = lines[i]
            for pid, what, blocker in _WAITS.findall(line):
                event["waits"].append({"pid": pid, "waits_for": what, "blocked_by": blocker})
            if m := _PROCESS_SQL.match(line):
                event["statements"][m.group(1)] = m.group(2).strip()
            i += 1
        events.append(event)
    return events


def summarize(max_examples: int = 3) -> dict:
    """Compact view for the model: total count plus a few distinct examples."""
    events = deadlock_events()
    seen, examples = set(), []
    for event in reversed(events):
        key = tuple(sorted(set(event["statements"].values())))
        if key in seen:
            continue
        seen.add(key)
        examples.append(event)
        if len(examples) == max_examples:
            break
    return {"total_deadlocks_in_log": len(events), "distinct_examples": examples}

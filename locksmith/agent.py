"""LockSmith agent: diagnose -> fix -> get an independent verdict, using Muse Spark tool calling.

The agent never decides whether its fix works. It can ask the harness for feedback on
practice seeds as often as it likes, but submit_fix triggers a fresh verification on
held-out seeds it has never seen, and only that verdict counts.

Usage: python -m locksmith.agent <scenario>
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from locksmith import budget, db, harness, logs
from locksmith.scenarios import SCENARIOS, Scenario

load_dotenv(db.ROOT / ".env")

WORKSPACE = db.ROOT / "workspace"
MODEL = os.environ.get("LOCKSMITH_MODEL", "muse-spark-1.3")
MAX_TURNS = 20
MAX_OUTPUT_TOKENS = 16_000  # bounds the cost of any single call

SYSTEM_PROMPT = """You are LockSmith, an engineer who fixes concurrency bugs in fintech services backed by PostgreSQL.
Workflow:
1. Call read_incident to see the production report and evidence from a load test of the current code.
2. Read the service code and schema with list_files and read_file.
3. Explain the root cause: which concurrent interleaving breaks which money rule.
4. Write a complete replacement for service.py with write_candidate.
   - Keep every function signature, return value and exception exactly as before; one request at a time,
     your version must behave identically to the original.
   - Fix the concurrency bug without serialising unrelated requests (no table locks or global locks):
     throughput is measured.
   - Do not rely on SERIALIZABLE or NOWAIT unless you also retry, because errors count as failures.
   - If you need schema changes (for example a unique index), define MIGRATIONS = [sql, ...] in service.py.
5. Call run_verification for feedback. If any check fails, read why, revise and test again.
6. When verification passes, call submit_fix. An independent verifier re-tests on unseen workloads and
   decides; if it rejects the fix, keep working.
Never claim a fix works unless submit_fix accepted it."""

TOOLS = [
    {
        "type": "function",
        "name": "read_incident",
        "description": "The production incident report plus evidence from load-testing the current code "
        "(and Postgres deadlock reports, if any).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "list_files",
        "description": "List the files of the service under repair.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a file of the service, by a name list_files returned.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "type": "function",
        "name": "write_candidate",
        "description": "Save a complete candidate replacement for service.py. Returns a candidate id.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "full Python source of the new service.py"}},
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "run_verification",
        "description": "Practice verification of a candidate: same behaviour as the original, safe under "
        "concurrent load on three workloads, and no throughput regression.",
        "parameters": {"type": "object", "properties": {"candidate_id": {"type": "string"}}, "required": ["candidate_id"]},
    },
    {
        "type": "function",
        "name": "submit_fix",
        "description": "Submit a candidate for the final, independent verdict on unseen workloads.",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "root_cause": {"type": "string"},
                "fix": {"type": "string"},
            },
            "required": ["candidate_id", "root_cause", "fix"],
        },
    },
]


# These tools only fail if our own infrastructure (Postgres, harness) is broken. Candidate
# bugs are reported inside their results, so an exception here means: stop, don't let the
# model spend tokens retrying something it cannot fix.
HARNESS_TOOLS = {"read_incident", "run_verification", "submit_fix"}


class InfrastructureError(RuntimeError):
    pass


class Session:
    """Tool implementations. The verdict comes from the harness, never from the model."""

    def __init__(self, scenario: Scenario, on_event=print):
        self.scenario = scenario
        self.on_event = on_event
        self.workspace = WORKSPACE / scenario.name
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.candidates: dict[str, Path] = {}
        self.accepted: dict | None = None

    def read_incident(self):
        evidence = harness.baseline(self.scenario.name)
        report = {"incident": self.scenario.incident, "load_test_of_current_code": evidence}
        if evidence["deadlock"]:
            report["postgres_deadlock_reports"] = logs.summarize()
        return report

    def list_files(self):
        return sorted(p.name for p in self.scenario.target_dir.iterdir() if p.suffix in (".py", ".sql"))

    def read_file(self, name):
        path = self.scenario.target_dir / Path(name).name
        if path.name not in self.list_files():
            raise ValueError(f"no such file: {name}")
        return path.read_text(encoding="utf-8")

    def write_candidate(self, code):
        candidate_id = f"c{len(self.candidates) + 1}"
        path = self.workspace / f"{candidate_id}_service.py"
        path.write_text(code, encoding="utf-8")
        self.candidates[candidate_id] = path
        return {"candidate_id": candidate_id}

    def run_verification(self, candidate_id):
        return harness.verify(self.scenario.name, self.candidates[candidate_id], harness.PRACTICE_SEEDS)

    def submit_fix(self, candidate_id, root_cause, fix):
        verdict = harness.verify(self.scenario.name, self.candidates[candidate_id], harness.HOLDOUT_SEEDS)
        if not verdict["passed"]:
            return {"accepted": False, "verdict": verdict}
        self.accepted = {
            "candidate_id": candidate_id,
            "path": str(self.candidates[candidate_id]),
            "root_cause": root_cause,
            "fix": fix,
            "verdict": verdict,
        }
        return {"accepted": True}

    def call(self, name: str, arguments: str) -> str:
        args = json.loads(arguments or "{}")
        self.on_event({"type": "tool_call", "name": name, "args": {k: v for k, v in args.items() if k != "code"}})
        try:
            result = getattr(self, name)(**args)
        except Exception as exc:
            if name in HARNESS_TOOLS:
                self.on_event({"type": "aborted", "reason": f"{name} failed: {type(exc).__name__}: {exc}"})
                raise InfrastructureError(f"{name} failed") from exc
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.on_event({"type": "tool_result", "name": name, "result": result})
        return json.dumps(result, default=str)


def run(scenario_name: str, on_event=print) -> Session:
    db.uri()  # make sure Postgres is up before spending any tokens
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
    session = Session(SCENARIOS[scenario_name], on_event)
    request = dict(model=MODEL, instructions=SYSTEM_PROMPT, tools=TOOLS, max_output_tokens=MAX_OUTPUT_TOKENS)

    response = budget.create_response(
        client, input="A production incident was escalated to you. Diagnose it and ship a verified fix.", **request
    )
    for _ in range(MAX_TURNS):
        if response.output_text:
            on_event({"type": "message", "text": response.output_text})
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls or session.accepted:
            break
        outputs = [
            {"type": "function_call_output", "call_id": c.call_id, "output": session.call(c.name, c.arguments)}
            for c in calls
        ]
        response = budget.create_response(client, previous_response_id=response.id, input=outputs, **request)
    on_event({"type": "budget", **budget.spent()})
    return session


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SCENARIOS:
        sys.exit(f"usage: python -m locksmith.agent <{'|'.join(SCENARIOS)}>")
    result = run(sys.argv[1])
    if result.accepted:
        print("\nACCEPTED by the independent verifier:", result.accepted["path"])
    else:
        print("\nNo fix was accepted.")

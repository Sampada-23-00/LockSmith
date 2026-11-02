"""LockSmith agent: detect -> locate -> reproduce -> fix -> verify, using Muse Spark tool calling.

The model never gets to declare success on its own. A fix is only accepted when
the stress test, run by our code, shows zero deadlocks and conserved money.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from locksmith import db, logs, stress

load_dotenv(db.ROOT / ".env")

TARGET_DIR = db.ROOT / "demo_bank"
WORKSPACE = db.ROOT / "workspace"
MODEL = os.environ.get("LOCKSMITH_MODEL", "muse-spark-1.3")
MAX_TURNS = 20

SYSTEM_PROMPT = """You are LockSmith, an engineer who fixes concurrency bugs in fintech code.
Workflow:
1. Call read_deadlock_logs to see what Postgres reported.
2. Find the responsible code with list_files and read_file.
3. Explain the lock cycle in one or two sentences.
4. Write a fixed version of the whole file with write_candidate. Keep the function signature and behaviour
   (including InsufficientFunds and the unknown-account check) identical; only change how locks are taken.
5. Call run_stress_test on your candidate. If it fails, read the result, revise, and test again.
6. When a candidate passes, call submit_fix with a short explanation.
Never claim a fix works without a passing run_stress_test result."""

TOOLS = [
    {
        "type": "function",
        "name": "read_deadlock_logs",
        "description": "Return the number of deadlocks in the Postgres log and a few distinct examples (wait-for cycle and SQL).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "list_files",
        "description": "List the Python files of the application under analysis.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a file of the application, by the name list_files returned.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "write_candidate",
        "description": "Save a complete candidate replacement for an application file. Returns a candidate id.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "file being replaced, e.g. transfers.py"},
                "code": {"type": "string", "description": "full Python source of the new file"},
            },
            "required": ["name", "code"],
        },
    },
    {
        "type": "function",
        "name": "run_stress_test",
        "description": "Run concurrent transfers against a candidate and report deadlocks, errors and whether money was conserved.",
        "parameters": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "type": "function",
        "name": "submit_fix",
        "description": "Submit a candidate that has passed run_stress_test, with an explanation of the root cause and the fix.",
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


class Session:
    """Tool implementations plus the verification gate."""

    def __init__(self, on_event=print):
        self.on_event = on_event
        self.candidates: dict[str, dict] = {}
        self.passed: set[str] = set()
        self.submitted: dict | None = None
        WORKSPACE.mkdir(exist_ok=True)

    def _app_file(self, name: str) -> Path:
        path = (TARGET_DIR / Path(name).name).resolve()
        if path.parent != TARGET_DIR.resolve() or path.suffix != ".py":
            raise ValueError(f"not an application file: {name}")
        return path

    def read_deadlock_logs(self):
        return logs.summarize()

    def list_files(self):
        return sorted(p.name for p in TARGET_DIR.glob("*.py"))

    def read_file(self, name):
        return self._app_file(name).read_text(encoding="utf-8")

    def write_candidate(self, name, code):
        self._app_file(name)  # validates the name
        candidate_id = f"c{len(self.candidates) + 1}"
        path = WORKSPACE / f"{candidate_id}_{Path(name).name}"
        path.write_text(code, encoding="utf-8")
        self.candidates[candidate_id] = {"name": Path(name).name, "path": path}
        return {"candidate_id": candidate_id}

    def run_stress_test(self, candidate_id):
        candidate = self.candidates[candidate_id]
        result = stress.run(candidate["path"])
        if result["passed"]:
            self.passed.add(candidate_id)
        return result

    def submit_fix(self, candidate_id, root_cause, fix):
        if candidate_id not in self.passed:
            return {"accepted": False, "reason": "candidate has not passed run_stress_test"}
        self.submitted = {"candidate_id": candidate_id, "root_cause": root_cause, "fix": fix, **self.candidates[candidate_id]}
        return {"accepted": True}

    def call(self, name: str, arguments: str) -> str:
        args = json.loads(arguments or "{}")
        self.on_event({"type": "tool_call", "name": name, "args": {k: v for k, v in args.items() if k != "code"}})
        try:
            result = getattr(self, name)(**args)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self.on_event({"type": "tool_result", "name": name, "result": result})
        return json.dumps(result, default=str)


def run(on_event=print) -> Session:
    client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
    session = Session(on_event)

    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input="Production is reporting deadlocks in the transfer service. Find the cause and ship a verified fix.",
        tools=TOOLS,
    )
    for _ in range(MAX_TURNS):
        calls = [item for item in response.output if item.type == "function_call"]
        if response.output_text:
            on_event({"type": "message", "text": response.output_text})
        if not calls or session.submitted:
            break
        outputs = [
            {"type": "function_call_output", "call_id": c.call_id, "output": session.call(c.name, c.arguments)}
            for c in calls
        ]
        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )
    return session


if __name__ == "__main__":
    result = run()
    if result.submitted:
        print("\nVerified fix:", result.submitted["path"])
    else:
        print("\nNo verified fix was produced.")

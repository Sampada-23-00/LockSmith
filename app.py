"""LockSmith dashboard.

Run: .\\.venv\\Scripts\\streamlit run app.py
"""
import difflib
import json

import streamlit as st

from locksmith import budget, harness
from locksmith.agent import WORKSPACE
from locksmith.benchmark import RESULTS_DIR, run_one
from locksmith.scenarios import SCENARIOS

st.set_page_config(page_title="LockSmith", page_icon="🔒", layout="wide")

STEP_LABELS = {
    "read_incident": "Read the incident and load-test evidence",
    "list_files": "List the service files",
    "read_file": "Read {name}",
    "write_repro": "Write a reproduction test",
    "run_repro": "Check the test catches the bug (and only the bug)",
    "write_candidate": "Write a fix",
    "run_verification": "Practice verification",
    "submit_fix": "Submit for the independent verdict",
}

CHECK_LABELS = {
    "same_behaviour": "Same behaviour as the original, one request at a time",
    "performance": "No slowdown (throughput vs original)",
    "agent_reproduction_test": "The agent's own reproduction test now passes",
    "loads": "Code loads",
}


# A plausible fix for each bug that a hurried engineer (or an AI) might ship. The verifier must reject each.
TRAPS = {
    "deadlock": ("deadlock_table_lock.py", "Lock the whole accounts table", "No two transfers can ever deadlock."),
    "lost_update": ("lost_update_serializable.py", "Switch to SERIALIZABLE isolation", "The database itself prevents lost updates."),
    "overdraft": ("overdraft_reject_all.py", "Reject withdrawals that could race", "No account can go negative."),
    "double_spend": (
        "double_spend_unique_no_conflict.py",
        "Add a unique index on the idempotency key",
        "The database refuses a second payment row.",
    ),
    "joint_account": (
        "joint_account_partner_lock.py",
        "Also lock the partner's account",
        "Both household accounts are now locked before checking.",
    ),
    "daily_limit": (
        "daily_limit_lock_rows.py",
        "Lock today's withdrawal rows before summing",
        "The rows being added up can't change underneath us.",
    ),
}
BAD_FIXES_DIR = RESULTS_DIR.parent / "tests" / "bad_fixes"


def check_label(name: str) -> str:
    if name.startswith("under_load_seed_"):
        return f"Safe under concurrent load (unseen workload #{name.rsplit('_', 1)[1]})"
    return CHECK_LABELS.get(name, name)


def load_results() -> dict[str, dict]:
    results = {}
    for name in SCENARIOS:
        path = RESULTS_DIR / f"{name}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            events_path = RESULTS_DIR / f"{name}.events.jsonl"
            record["events"] = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
            results[name] = record
    return results


def result_summary(name: str, result) -> str:
    """One line describing a tool result, for the timeline."""
    if not isinstance(result, dict):
        return ""
    if "error" in result:
        return f"⚠️ {result['error']}"
    if name == "read_incident":
        problems = result["load_test_of_current_code"]["problems"]
        return "Load test of current code: " + ("; ".join(problems[:2]) if problems else "no problems found")
    if name == "run_repro":
        verdict = "✅ valid" if result["valid"] else "❌ not trusted"
        return (
            f"{verdict} — caught the bug {result['detects_bug_in_original_code']} runs, "
            f"false alarms on correct code {result['false_alarms_on_a_correct_implementation']}"
        )
    if name in ("run_verification",):
        checks = result.get("checks", [])
        return f"{sum(c['passed'] for c in checks)}/{len(checks)} checks passed"
    if name == "submit_fix":
        return "✅ ACCEPTED by the independent verifier" if result.get("accepted") else "❌ rejected"
    return ""


def render_event(event: dict, container) -> None:
    kind = event["type"]
    if kind == "message":
        container.markdown(f"> 💬 *{event['text']}*")
    elif kind == "tool_call":
        label = STEP_LABELS.get(event["name"], event["name"]).format(**event.get("args", {}))
        container.markdown(f"**▸ {label}**")
    elif kind == "tool_result":
        summary = result_summary(event["name"], event["result"])
        if summary:
            container.caption(summary)
    elif kind == "aborted":
        container.error(event["reason"])


def render_problems(problems: list[str], ok_text: str) -> None:
    if problems:
        for p in problems:
            st.markdown(f"🔴 {p}")
    else:
        st.markdown(f"🟢 {ok_text}")


# --- Header -----------------------------------------------------------------------

st.title("🔒 LockSmith")
st.markdown(
    "**An AI agent that diagnoses and repairs concurrency bugs in money-moving code — "
    "and an independent verifier that proves every fix before it is accepted.**  \n"
    "Powered by Muse Spark via the Meta Model API."
)

results = load_results()
if results:
    accepted = sum(r["accepted"] for r in results.values())
    checks = [c for r in results.values() if r.get("verdict") for c in r["verdict"]["checks"]]
    cols = st.columns(4)
    cols[0].metric("Bugs fixed autonomously", f"{accepted}/{len(SCENARIOS)}")
    cols[1].metric("Independent checks passed", f"{sum(c['passed'] for c in checks)}/{len(checks)}")
    cols[2].metric("Total model cost", f"${sum(r['cost_usd'] for r in results.values()):.2f}")
    cols[3].metric("Fix attempts needed", f"{sum(r['fix_attempts'] for r in results.values())} for {len(results)} bugs")

summary_path = RESULTS_DIR / "summary.json"
if summary_path.exists():
    with st.expander("Reliability across repeated runs and models"):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        st.dataframe(
            [
                {
                    "Model": model,
                    "Runs": m["runs"],
                    "Accepted": f"{m['accepted']}/{m['runs']}",
                    "Fixed on 1st attempt": f"{m['first_try_fixes']}/{m['runs']}",
                    "Mean cost / bug": f"${m['mean_cost_usd']:.3f}",
                    "Mean time / bug": f"{m['mean_minutes']} min",
                }
                for model, m in summary["models"].items()
            ],
            hide_index=True,
        )

with st.expander("How it works"):
    st.markdown(
        """
1. **Diagnose** — the agent reads the incident, load-test evidence, Postgres deadlock reports and the code.
2. **Prove the bug first** — it writes its own reproduction test. The harness trusts it only if it catches the bug
   on *every* run against the original code and *never* fires against a correct implementation.
3. **Fix** — it writes a replacement service.
4. **Independent verdict** — the agent cannot declare success. A plain-Python verifier re-tests the fix on workloads
   the agent has never seen: same behaviour as the original, no deadlocks or errors under load, no balance ever
   negative, every account reconciles, no slowdown, and the agent's own test now passes.
"""
    )

# --- Scenario -----------------------------------------------------------------------

st.divider()
name = st.radio(
    "Bug", list(SCENARIOS), format_func=lambda n: SCENARIOS[n].title, horizontal=True, label_visibility="collapsed"
)
scenario = SCENARIOS[name]
record = results.get(name)

st.subheader(scenario.title)
st.info(f"**Incident report:** {scenario.incident}")

tab_story, tab_test, tab_fix, tab_verdict, tab_live = st.tabs(
    ["Agent's investigation", "Reproduction test", "The fix", "Independent verdict", "Run it live"]
)

with tab_story:
    if not record:
        st.warning("No recorded run yet. Use **Run it live**.")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Verdict", "Accepted" if record["accepted"] else "Not accepted")
        c2.metric("Tests written", record["repro_attempts"])
        c3.metric("Fixes written", record["fix_attempts"])
        c4.metric("Time", f"{record['seconds'] / 60:.1f} min")
        c5.metric("Model cost", f"${record['cost_usd']:.3f}")
        if record.get("root_cause"):
            st.markdown(f"**Root cause (agent):** {record['root_cause']}")
        st.markdown("#### Step by step")
        steps = st.container(border=True)
        for event in record["events"]:
            render_event(event, steps)

with tab_test:
    if record and record.get("repro_code"):
        report = record["repro_report"]
        c1, c2 = st.columns(2)
        c1.metric("Caught the bug in the original code", report["detects_bug_in_original_code"])
        c2.metric("False alarms on a correct implementation", report["false_alarms_on_a_correct_implementation"])
        st.markdown(f"**What it caught:** {report['example_violation']}")
        st.caption("Written by the agent before it was allowed to fix anything.")
        st.code(record["repro_code"], language="python")
    else:
        st.warning("No accepted run yet.")

with tab_fix:
    if record and record.get("fix_code"):
        st.markdown(f"**Fix (agent):** {record['fix']}")
        original = scenario.service_path.read_text(encoding="utf-8").splitlines(keepends=True)
        fixed = record["fix_code"].splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(original, fixed, "service.py (original)", "service.py (agent's fix)"))
        st.code(diff, language="diff")
    else:
        st.warning("No accepted run yet.")

with tab_verdict:
    if record and record.get("verdict"):
        st.caption("Decided by the harness, not the model. Workloads #101–103 were never shown to the agent.")
        for check in record["verdict"]["checks"]:
            icon = "✅" if check["passed"] else "❌"
            with st.expander(f"{icon} {check_label(check['name'])}"):
                st.json(check["detail"])
        perf = next((c["detail"] for c in record["verdict"]["checks"] if c["name"] == "performance"), None)
        if perf:
            st.markdown(
                f"**Throughput:** original {perf['original_ops_per_s']} ops/s → fix {perf['fix_ops_per_s']} ops/s "
                f"(**{perf['ratio']}×**, minimum allowed {perf['required_ratio']}×)"
            )
    else:
        st.warning("No accepted run yet.")

with tab_live:
    st.markdown("#### Before vs after, on a fresh workload")
    st.caption(
        f"{harness.WORKERS} concurrent workers × {harness.OPS_PER_WORKER} requests on hot accounts. "
        "Runs locally, no model calls."
    )
    seed = st.number_input("Workload seed", min_value=1, max_value=10_000, value=2026)
    if st.button("Run original vs fix", type="primary", disabled=not (record and record.get("fix_code"))):
        WORKSPACE.mkdir(exist_ok=True)
        fix_path = WORKSPACE / f"live_{name}_fix.py"
        fix_path.write_text(record["fix_code"], encoding="utf-8")
        plan = scenario.contended(seed, harness.WORKERS, harness.OPS_PER_WORKER)
        left, right = st.columns(2)
        with left:
            st.markdown("##### ❌ Original code")
            with st.spinner("Running..."):
                before = harness.run_workload(scenario, scenario.service_path, plan)
            st.metric("Money rules broken", len(before["problems"]))
            st.metric("Requests failed (deadlock or error)", before["deadlock"] + before["error"])
            st.metric("Throughput", f"{before['throughput_ops_per_s']} ops/s")
            render_problems(before["problems"][:6], "No problems")
        with right:
            st.markdown("##### ✅ Agent's fix")
            with st.spinner("Running..."):
                after = harness.run_workload(scenario, fix_path, plan)
            st.metric("Money rules broken", len(after["problems"]))
            st.metric("Requests failed (deadlock or error)", after["deadlock"] + after["error"])
            st.metric("Throughput", f"{after['throughput_ops_per_s']} ops/s")
            render_problems(after["problems"][:6], "No deadlocks, no errors, no negative balances, every account reconciles")
        fix_path.unlink(missing_ok=True)

    st.divider()
    trap_file, trap_title, trap_appeal = TRAPS[name]
    st.markdown("#### Would the verifier catch a wrong fix?")
    st.markdown(f"**The obvious fix:** {trap_title}. *Why it looks right:* {trap_appeal}")
    with st.expander("See the code"):
        st.code((BAD_FIXES_DIR / trap_file).read_text(encoding="utf-8"), language="python")
    if st.button("Submit this fix to the verifier"):
        with st.spinner("Verifying (up to a minute)..."):
            verdict = harness.verify(name, BAD_FIXES_DIR / trap_file, harness.HOLDOUT_SEEDS)
        if verdict["passed"]:
            st.error("The verifier accepted it. This should not happen.")
        else:
            st.error("❌ REJECTED by the independent verifier")
        for check in verdict["checks"]:
            if check["passed"]:
                st.markdown(f"✅ {check_label(check['name'])}")
            else:
                detail = check["detail"]
                if check["name"] == "performance":
                    reason = f"{detail['ratio']}× the original's throughput (minimum {detail['required_ratio']}×)"
                elif isinstance(detail, dict):
                    reason = "; ".join(detail["problems"][:2])
                else:
                    reason = "; ".join(detail) if isinstance(detail, list) else str(detail)
                st.markdown(f"❌ **{check_label(check['name'])}** — {reason}")

    st.divider()
    st.markdown("#### Run the agent live")
    spent = budget.spent()
    daily, total = budget.limits()
    st.caption(
        f"Calls Muse Spark (about \\$0.08 per bug). Spent today \\${spent['today_usd']:.2f} of \\${daily:.2f}, "
        f"total \\${spent['total_usd']:.2f} of \\${total:.2f}. Takes 2–5 minutes."
    )
    if st.button("Run the agent on this bug"):
        live = st.container(border=True)
        with st.spinner("Agent working..."):
            outcome = run_one(name, show=lambda e: render_event(e, live))
        if outcome["accepted"]:
            st.success("Fix accepted by the independent verifier. Results saved.")
        else:
            st.error(f"No fix accepted. {outcome.get('aborted') or ''}")

# LockSmith

An AI agent that diagnoses and repairs concurrency bugs in money-moving code, and an independent verifier that proves each fix before it is accepted.

Built for the Meta Global AI Developer Hackathon (Indie track), Oct 27 – Nov 4, 2026, using **Muse Spark** via the Meta Model API.

## Why

AI coding agents verify fixes by rerunning tests. Concurrency bugs only appear under particular timings, so a test can pass by luck. In payment systems that luck costs real money. LockSmith never lets the agent judge its own work.

## The benchmark: four real fintech concurrency bugs on PostgreSQL

| Scenario | What goes wrong | Reproduced (6 seeds) |
|---|---|---|
| `deadlock` | Opposite-direction transfers lock rows in different orders and kill each other | 6/6 |
| `lost_update` | Balance read into the app and written back; concurrent updates vanish | 6/6 |
| `overdraft` | Balance check and debit not atomic; accounts go negative | 6/6 |
| `double_spend` | Idempotency check is a plain SELECT; client retries charge twice | 6/6 |

Each buggy service lives in `benchmark/<scenario>/` (what the agent may read and replace). Workloads and money checks live in `locksmith/scenarios.py` (never shown to the agent).

## Results (Muse Spark 1.3)

| Bug | Agent's reproduction test | Fix attempts | Independent verdict | Cost |
|---|---|---|---|---|
| Deadlock | valid on 1st try | 1 | accepted (6/6 checks) | $0.056 |
| Lost update | valid on 1st try | 1 | accepted (6/6 checks) | $0.055 |
| Overdraft | 1st test caught the bug only 1/3 runs and was **rejected as unreliable**; 2nd caught it 3/3 | 1 | accepted (6/6 checks) | $0.065 |
| Double spend | valid on 1st try | 1 | accepted (6/6 checks) | $0.054 |

4/4 bugs fixed autonomously for **$0.23** in total. Full transcripts, the agent's tests and fixes are in `results/`.

## How the agent works (Muse Spark, tool calling)

1. **Diagnose**: read the incident report, load-test evidence, Postgres deadlock reports and the code.
2. **Prove the bug first**: write its own reproduction test. The harness only trusts it if it catches the bug in the original code on **every** run and **never** fires on a correct implementation. A test that always fails proves nothing.
3. **Fix**: write a replacement service (schema changes allowed via `MIGRATIONS`).
4. **Practice**: get feedback from the verifier on practice workloads, as often as needed.
5. **Submit**: the independent verifier re-tests on held-out workloads and runs the agent's own reproduction test against the fix. Only that verdict counts.

## The independent verifier

A candidate fix is accepted only if **all** checks pass (`locksmith/harness.py`):

1. **Same behaviour**: run sequentially, it returns exactly what the original returns and leaves an identical database.
2. **Safe under load**: 16 workers × 30 requests on hot accounts, three different workloads: no deadlocks, no unexpected errors, no balance ever negative (audited on every write by a hidden trigger), and every account matches the sum of its successful operations.
3. **Not slower**: throughput on a low-contention workload stays at least 50% of the original (simulated 2 ms app-to-database latency, as in production).

4. **Its own proof holds**: the agent's reproduction test, which failed on the original code, now passes 3/3.

The agent may run practice verification on seeds 1–3. `submit_fix` re-verifies on held-out seeds 101–103 it has never seen; only that verdict counts. Agent-written tests run in a separate process with a timeout.

The verifier is itself tested (`tests/test_harness.py`): it reproduces every bug, accepts a correct reference fix for each, and rejects plausible bad fixes for the right reason:

| Bad fix | Looks like it works because | Rejected by |
|---|---|---|
| Lock the whole table | No more deadlocks | performance (6× slower) |
| Reject every withdrawal | No account can go negative | same behaviour |
| SERIALIZABLE without retry | No lost updates | under load (customers get errors) |

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # then add your MODEL_API_KEY

.\.venv\Scripts\python -m locksmith.harness baseline           # reproduce all four bugs (no API needed)
.\.venv\Scripts\python -m pytest -q tests                      # prove the verifier is trustworthy
.\.venv\Scripts\python -m locksmith.agent overdraft            # let the agent fix one
.\.venv\Scripts\python -m locksmith.benchmark                  # run the agent on all four, results in results/
```

Postgres runs embedded via `pgserver`, so nothing needs installing. API spend is capped per day and in total by `locksmith/budget.py`.

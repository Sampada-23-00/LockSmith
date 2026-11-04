# LockSmith

An AI agent that diagnoses and repairs concurrency bugs in money-moving code, and an independent verifier that proves each fix before it is accepted.

Built for the Meta Global AI Developer Hackathon (Indie track), Oct 27 – Nov 4, 2026, using **Muse Spark** via the Meta Model API.

## Why

AI coding agents verify fixes by rerunning tests. Concurrency bugs only appear under particular timings, so a test can pass by luck. In payment systems that luck costs real money. LockSmith never lets the agent judge its own work.

## The benchmark: six real fintech concurrency bugs on PostgreSQL

| Scenario | What goes wrong | Reproduced (6 seeds) |
|---|---|---|
| `deadlock` | Opposite-direction transfers lock rows in different orders and kill each other | 6/6 |
| `lost_update` | Balance read into the app and written back; concurrent updates vanish | 6/6 |
| `overdraft` | Balance check and debit not atomic; accounts go negative | 6/6 |
| `double_spend` | Idempotency check is a plain SELECT; client retries charge twice | 6/6 |
| `joint_account` | Each partner locks only their own account while checking the household total; the household goes negative (write skew) | 6/6 |
| `daily_limit` | The daily total is summed from rows that concurrent requests have not inserted yet; the limit is exceeded (phantom) | 6/6 |

Each buggy service lives in `benchmark/<scenario>/` (what the agent may read and replace). Workloads and money checks live in `locksmith/scenarios.py` (never shown to the agent).

## Results (Muse Spark 1.3, no hints in the code)

| Bug | Reproduction tests written | Fix attempts | Independent verdict | Cost |
|---|---|---|---|---|
| Deadlock | 3 (first two rejected as unreliable) | 1 | accepted (6/6 checks) | $0.15 |
| Lost update | 2 | 1 | accepted (6/6 checks) | $0.16 |
| Overdraft | 2 | 1 | accepted (6/6 checks) | $0.18 |
| Double spend | 1 | 1 | accepted (6/6 checks) | $0.05 |
| Household overdraft (write skew) | 1 | 1 | accepted (6/6 checks) | $0.07 |
| Daily limit bypass (phantom) | 1 | 1 | accepted (6/6 checks) | $0.07 |

6/6 bugs fixed autonomously for **$0.69** in total. The benchmark services carry no comments describing their bugs. Full transcripts, the agent's tests and fixes are in `results/`.

The two harder bugs have an obvious fix that is wrong, and the verifier rejects both: locking the partner's account too deadlocks under load, and locking today's withdrawal rows cannot stop new rows from being inserted. The agent avoided both.

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
| Also lock the partner's account | Both household rows are locked | under load (deadlocks) |
| Lock today's withdrawal rows | The rows being summed are locked | under load (limit still exceeded) |

## From prototype to production

LockSmith today is a working prototype on six self-contained services. This is how it maps to a real payments stack, and what is not solved yet.

| Today | In production |
|---|---|
| One `service.py` per bug | The agent gets code search over the real repository; the verifier drives the service through its API, so the language and ORM don't matter |
| Embedded Postgres with seeded accounts | A disposable copy built from the company's own migrations, with synthetic or anonymised data. **The verifier never touches the production database** |
| Workloads written per scenario | Workloads derived from production traffic shape (which accounts are hot, how requests overlap), anonymised |
| Money rules written in Python | Rules as configuration owned by finance/risk: balance never negative, one charge per end-to-end id, daily limits |
| "Accepted" | A pull request with the evidence attached (reproduction test, before/after numbers, verdict) for human review, then normal CI and staged rollout. **Nothing is auto-deployed** |
| Hosted Muse Spark via the Meta Model API | Banks that cannot send code outside can self-host Meta's open-weight models (Muse Spark Open, Muse Glimmer) behind the same OpenAI-compatible interface |

**Limitations, stated plainly**

- Testing shows a bug exists; it cannot prove one is impossible. LockSmith reduces luck (3/3 reproduction, barrier-synchronised tests, held-out workloads, every-write audits) but its verdict is strong evidence, not a formal proof.
- Scope is concurrency inside one PostgreSQL database. Cross-service races (queues, sagas, read replicas) are out of scope.
- The six bugs are planted, so ground truth is known. The service code carries no hints, the workloads and money checks are hidden from the agent, and two bugs are traps where the obvious fix is wrong.

## Standards mapping

| LockSmith | Industry standard |
|---|---|
| Amounts stored as integer paise | ISO 4217 minor units (INR, exponent 2); no floating point anywhere |
| `idempotency_key` on payments | ISO 20022 `EndToEndId` / UETR: the identifier that lets a retried payment be recognised as the same payment |
| Transfer, withdrawal and payment flows | ISO 20022 credit transfer (`pacs.008`) and customer payment initiation (`pain.001`) |

## FAQ

**Aren't these toy bugs?** They are small on purpose: a benchmark needs known ground truth. Each one is a real, documented failure mode in payment systems, reproduces on every seed, and two have tempting wrong fixes the verifier rejects.

**Why not just use SERIALIZABLE everywhere?** Without retries, clients get serialization errors under load; the verifier rejects that fix. With retries it can be valid, and the agent is free to choose it.

**Can the agent game the verifier?** It never sees the workloads, the money checks or the held-out seeds, cannot read the harness code, and its own reproduction test must fail on the original code and pass on a correct implementation before it counts.

**What does it cost?** About $0.12 (roughly ₹10) per bug with Muse Spark, measured in `results/`.

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

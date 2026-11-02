# LockSmith

An AI agent that finds, reproduces and fixes deadlocks in fintech transaction code, and proves each fix works before accepting it.

Built for the Meta Global AI Developer Hackathon (Indie track), Oct 27 – Nov 4, 2026, using **Muse Spark** via the Meta Model API.

## How it works

1. **Detect**: parse real Postgres logs (`ERROR: deadlock detected`) into wait-for cycles and SQL.
2. **Locate**: the agent reads the application code to find the transactions involved.
3. **Fix**: it writes a candidate patch.
4. **Verify**: a concurrent stress test (16 workers × 50 transfers on hot accounts) must show zero deadlocks, zero errors and conserved money.
5. **Gate**: `submit_fix` is refused unless the candidate passed the stress test. The model cannot declare success on its own.

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # then add your MODEL_API_KEY

# Reproduce the planted bug (no API needed)
.\.venv\Scripts\python -m locksmith.stress

# Let the agent fix it
.\.venv\Scripts\python -m locksmith.agent
```

Postgres runs embedded via `pgserver`, so nothing needs installing.

## Baseline

| Version | Transfers | Deadlocks | Money conserved | Time |
|---|---|---|---|---|
| `demo_bank/transfers.py` (planted bug) | 800 | 538 | yes | 28.6 s |
| Sorted-lock fix (`tests/fixtures`) | 800 | 0 | yes | 1.8 s |

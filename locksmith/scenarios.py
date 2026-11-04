"""Benchmark scenarios: four real fintech concurrency bugs.

Each scenario pairs a buggy service in benchmark/<name>/ (what the agent reads and
may replace) with a workload and money checks defined here (what the agent never
sees or changes).
"""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psycopg

from locksmith import db

BENCHMARK_DIR = db.ROOT / "benchmark"

Op = tuple[str, dict]  # (service function name, keyword arguments)
Plan = list[list[Op]]  # one list of operations per worker
Check = Callable[[psycopg.Connection, list[dict], dict[int, int]], list[str]]


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    incident: str  # what production reports, in the words an on-call engineer would see
    contended: Callable[[int, int, int], Plan]  # (seed, workers, ops_per_worker): hot accounts, bug-prone
    calm: Callable[[int, int, int], Plan]  # each worker on its own accounts, used to measure speed
    check: Check
    start_balance: int = 1_000_00
    num_accounts: int = 64
    business_errors: tuple[str, ...] = ("InsufficientFunds",)
    negative_balances_allowed: bool = False  # e.g. one partner may dip below zero within a household

    @property
    def target_dir(self) -> Path:
        return BENCHMARK_DIR / self.name

    @property
    def service_path(self) -> Path:
        return self.target_dir / "service.py"

    @property
    def schema_sql(self) -> str:
        return (self.target_dir / "schema.sql").read_text(encoding="utf-8")

    @property
    def reference_path(self) -> Path:
        """A known-correct implementation, used only to check that tests don't raise false alarms."""
        return db.ROOT / "tests" / "reference_fixes" / f"{self.name}.py"


def balances(conn: psycopg.Connection) -> dict[int, int]:
    return dict(conn.execute("SELECT id, balance FROM accounts").fetchall())


def ledger_check(effects: Callable[[str, dict], dict[int, int]]) -> Check:
    """Every account must end exactly where the successful operations say it should."""

    def check(conn, results, start):
        expected = dict(start)
        for r in results:
            if r["outcome"] == "ok":
                for account, delta in effects(r["fn"], r["kwargs"]).items():
                    expected[account] += delta
        actual = balances(conn)
        return [
            f"account {a}: balance is {actual[a]} but successful operations add up to {expected[a]}"
            for a in sorted(expected)
            if actual[a] != expected[a]
        ]

    return check


def _per_worker(seed: int, workers: int, make_op: Callable[[random.Random, int, int], Op], ops: int) -> Plan:
    plan = []
    for w in range(workers):
        rng = random.Random(seed * 1000 + w)
        plan.append([make_op(rng, w, j) for j in range(ops)])
    return plan


# --- 1. Deadlock: transfers lock rows in request order -------------------------

def _transfer_effects(fn, kw):
    return {kw["from_id"]: -kw["amount"], kw["to_id"]: kw["amount"]}


def _transfers_contended(seed, workers, ops):
    def op(rng, w, j):
        a, b = rng.sample(range(1, 5), 2)
        return ("transfer", {"from_id": a, "to_id": b, "amount": rng.randint(1, 500)})

    return _per_worker(seed, workers, op, ops)


def _transfers_calm(seed, workers, ops):
    def op(rng, w, j):
        a, b = 2 * w + 1, 2 * w + 2
        if j % 2:
            a, b = b, a
        return ("transfer", {"from_id": a, "to_id": b, "amount": rng.randint(1, 500)})

    return _per_worker(seed, workers, op, ops)


# --- 2. Lost update: read-modify-write of the balance in Python -----------------

def _wallet_effects(fn, kw):
    return {kw["account_id"]: kw["amount"] if fn == "deposit" else -kw["amount"]}


def _wallet_contended(seed, workers, ops):
    def op(rng, w, j):
        return (rng.choice(["deposit", "withdraw"]), {"account_id": rng.randint(1, 2), "amount": rng.randint(1, 500)})

    return _per_worker(seed, workers, op, ops)


def _wallet_calm(seed, workers, ops):
    def op(rng, w, j):
        return (rng.choice(["deposit", "withdraw"]), {"account_id": w + 1, "amount": rng.randint(1, 500)})

    return _per_worker(seed, workers, op, ops)


# --- 3. Overdraft race: balance check and debit are not atomic ------------------

def _overdraft_contended(seed, workers, ops):
    # Balances hover near zero (₹10 start, refilled by deposits), so withdrawals race at the edge
    def op(rng, w, j):
        account = rng.randint(1, 2)
        if rng.random() < 0.45:
            return ("deposit", {"account_id": account, "amount": rng.randint(200, 400)})
        return ("withdraw", {"account_id": account, "amount": rng.randint(200, 400)})

    return _per_worker(seed, workers, op, ops)


def _overdraft_calm(seed, workers, ops):
    def op(rng, w, j):
        return (rng.choice(["deposit", "withdraw"]), {"account_id": w + 1, "amount": rng.randint(1, 5)})

    return _per_worker(seed, workers, op, ops)


# --- 4. Double spend: idempotency check is a plain SELECT -----------------------

def _payments_contended(seed, workers, ops):
    # Workers in groups of 3 send the same requests in the same order: a client retrying twice
    plan = []
    for w in range(workers):
        group = w // 3
        account = group % 4 + 1
        worker_ops = []
        for j in range(ops):
            amount = random.Random(f"{seed}-{group}-{j}").randint(1, 500)
            worker_ops.append(
                ("pay", {"idempotency_key": f"s{seed}-g{group}-{j}", "account_id": account, "amount": amount})
            )
        plan.append(worker_ops)
    return plan


def _payments_calm(seed, workers, ops):
    def op(rng, w, j):
        return ("pay", {"idempotency_key": f"c{seed}-w{w}-{j}", "account_id": w + 1, "amount": rng.randint(1, 500)})

    return _per_worker(seed, workers, op, ops)


def _payments_check(conn, results, start):
    problems = [
        f"payment {key} was charged {n} times"
        for key, n in conn.execute(
            "SELECT idempotency_key, COUNT(*) FROM payments GROUP BY 1 HAVING COUNT(*) > 1 ORDER BY 1"
        )
    ]
    charged = {a: int(s) for a, s in conn.execute("SELECT account_id, SUM(amount) FROM payments GROUP BY 1")}
    actual = balances(conn)
    problems += [
        f"account {a}: balance is {actual[a]} but recorded payments add up to {s - charged.get(a, 0)}"
        for a, s in sorted(start.items())
        if actual[a] != s - charged.get(a, 0)
    ]
    recorded = {k for (k,) in conn.execute("SELECT DISTINCT idempotency_key FROM payments")}
    lost = {r["kwargs"]["idempotency_key"] for r in results if r["outcome"] == "ok"} - recorded
    problems += [f"payment {k} was acknowledged but never recorded" for k in sorted(lost)]
    return problems


# --- 5. Write skew: shared household overdraft rule ------------------------------

def _household_contended(seed, workers, ops):
    # Two workers per household, one per partner, both spending at the same time.
    # Withdrawals only, so a household total that ever went negative stays negative at the end.
    def op(rng, w, j):
        household = w // 2 + 1
        return ("withdraw", {"account_id": 2 * household - 1 + w % 2, "amount": rng.randint(100, 300)})

    return _per_worker(seed, workers, op, ops)


def _household_calm(seed, workers, ops):
    def op(rng, w, j):
        account = 2 * w + 1 + j % 2  # both partners of the worker's own household
        return (rng.choice(["deposit", "withdraw"]), {"account_id": account, "amount": rng.randint(1, 5)})

    return _per_worker(seed, workers, op, ops)


def _household_check(conn, results, start):
    problems = [
        f"household {h} (accounts {2 * h - 1}+{2 * h}) is overdrawn: combined balance {total}"
        for h, total in conn.execute(
            "SELECT household, SUM(balance) FROM accounts GROUP BY household HAVING SUM(balance) < 0 ORDER BY 1"
        )
    ]
    return problems + ledger_check(_wallet_effects)(conn, results, start)


# --- 6. Phantom: daily limit summed over rows that don't exist yet ----------------

DAILY_LIMIT = 5_000  # must match benchmark/daily_limit/service.py


def _daily_contended(seed, workers, ops):
    def op(rng, w, j):
        return ("withdraw", {"account_id": rng.randint(1, 4), "amount": rng.randint(500, 1_500)})

    return _per_worker(seed, workers, op, ops)


def _daily_calm(seed, workers, ops):
    def op(rng, w, j):
        return ("withdraw", {"account_id": w + 1, "amount": rng.randint(1, 10)})

    return _per_worker(seed, workers, op, ops)


def _daily_check(conn, results, start):
    problems = [
        f"account {a} withdrew {total} today, above the daily limit of {DAILY_LIMIT}"
        for a, total in conn.execute(
            "SELECT account_id, SUM(amount) FROM withdrawals WHERE day = current_date "
            "GROUP BY account_id HAVING SUM(amount) > %s ORDER BY 1",
            (DAILY_LIMIT,),
        )
    ]
    return problems + ledger_check(_wallet_effects)(conn, results, start)


SCENARIOS = {
    s.name: s
    for s in [
        Scenario(
            name="deadlock",
            title="Deadlock in money transfers",
            incident="Transfers between the same customers in opposite directions intermittently fail with "
            "'deadlock detected'. Customers see failed payments during peak hours.",
            contended=_transfers_contended,
            calm=_transfers_calm,
            check=ledger_check(_transfer_effects),
        ),
        Scenario(
            name="lost_update",
            title="Lost update in wallet top-ups",
            incident="Finance reconciliation is off: wallet balances do not match the sum of successful "
            "top-ups and spends. No errors are logged.",
            contended=_wallet_contended,
            calm=_wallet_calm,
            check=ledger_check(_wallet_effects),
        ),
        Scenario(
            name="overdraft",
            title="Overdraft race in withdrawals",
            incident="Some accounts without an overdraft facility have negative balances, even though every "
            "withdrawal checks the balance first.",
            contended=_overdraft_contended,
            calm=_overdraft_calm,
            check=ledger_check(_wallet_effects),
            start_balance=1_000,
        ),
        Scenario(
            name="double_spend",
            title="Double charge on payment retries",
            incident="Customers report being charged twice for one purchase. It happens when the app retries "
            "a payment after a timeout, even though requests carry an idempotency key.",
            contended=_payments_contended,
            calm=_payments_calm,
            check=_payments_check,
        ),
        Scenario(
            name="joint_account",
            title="Household overdraft (write skew)",
            incident="Some households have a negative combined balance, which our rules forbid. Each withdrawal "
            "checks the household's combined balance and locks the account it debits.",
            contended=_household_contended,
            calm=_household_calm,
            check=_household_check,
            start_balance=1_000,
            negative_balances_allowed=True,
        ),
        Scenario(
            name="daily_limit",
            title="Daily withdrawal limit bypass (phantom)",
            incident="Risk team found accounts that withdrew more than the ₹50 daily limit in one day. Every "
            "withdrawal checks today's total before it is approved.",
            contended=_daily_contended,
            calm=_daily_calm,
            check=_daily_check,
            business_errors=("DailyLimitExceeded",),
        ),
    ]
}

"""The verifier itself must be trustworthy: it has to catch every planted bug,
accept correct fixes, and reject plausible-looking bad fixes for the right reason."""
from pathlib import Path

import pytest

from locksmith.harness import HOLDOUT_SEEDS, PRACTICE_SEEDS, baseline, verify
from locksmith.scenarios import SCENARIOS

TESTS = Path(__file__).parent


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s != "deadlock"])
def test_bug_reproduces_on_every_seed(scenario):
    for seed in PRACTICE_SEEDS + HOLDOUT_SEEDS:
        assert baseline(scenario, seed)["bug_reproduced"], f"{scenario} seed {seed}"


def test_deadlock_reproduces():
    # One seed only: each deadlock run takes ~13s because of lock waits
    assert baseline("deadlock")["bug_reproduced"]


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_reference_fix_is_accepted(scenario):
    report = verify(scenario, TESTS / "reference_fixes" / f"{scenario}.py")
    assert report["passed"], [c for c in report["checks"] if not c["passed"]]


@pytest.mark.parametrize(
    "scenario, bad_fix, failing_check",
    [
        ("deadlock", "deadlock_table_lock.py", "performance"),
        ("overdraft", "overdraft_reject_all.py", "same_behaviour"),
        ("lost_update", "lost_update_serializable.py", "under_load_seed_1"),
    ],
)
def test_bad_fix_is_rejected_for_the_right_reason(scenario, bad_fix, failing_check):
    report = verify(scenario, TESTS / "bad_fixes" / bad_fix)
    assert not report["passed"]
    assert failing_check in [c["name"] for c in report["checks"] if not c["passed"]]

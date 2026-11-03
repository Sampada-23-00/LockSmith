"""Reproduction tests must catch the real bug and nothing else."""
from pathlib import Path

import pytest

from locksmith import repro
from locksmith.scenarios import SCENARIOS

TESTS = Path(__file__).parent


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_reference_repro_is_valid(scenario):
    report = repro.evaluate(scenario, TESTS / "reference_repros" / f"{scenario}.py")
    assert report["valid"], report


def test_test_that_always_fails_is_rejected():
    report = repro.evaluate("overdraft", TESTS / "bad_repros" / "always_fails.py")
    assert not report["valid"]
    assert report["false_alarms_on_a_correct_implementation"] == "3/3"


def test_fixed_code_passes_the_repro():
    check = repro.check_candidate(
        "overdraft", SCENARIOS["overdraft"].reference_path, TESTS / "reference_repros" / "overdraft.py"
    )
    assert check["passed"], check

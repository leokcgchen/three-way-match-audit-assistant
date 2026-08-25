from src.workflow.pipeline import cutoff_calendar_mode
from src.rules.cutoff_checker import CutoffChecker
from src.workflow.sample_desk import sample_tests_failed


def test_gospd01030_defaults_cutoff_to_period_end_only_without_changing_other_goals() -> None:
    assert cutoff_calendar_mode(["gospd01030"], None) == "period_end_only"
    assert cutoff_calendar_mode(["gospd01010"], None) is None
    assert cutoff_calendar_mode(["gospd01030"], "fiscal_445") == "fiscal_445"


def test_gospd01030_year_end_boundary_accepts_dates_on_the_same_side() -> None:
    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2025-11-30",
        entry_date="2025-12-01",
        period_end="2025-12-31",
        calendar_mode=cutoff_calendar_mode(["gospd01030"], None),
    )

    assert result.test_status == "PASS"


def test_chronology_fail_blocks_sample_auto_confirmation() -> None:
    assert sample_tests_failed({"three_way": {"date_chronology": {"status": "FAIL"}}})

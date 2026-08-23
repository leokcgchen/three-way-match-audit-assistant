"""Job 落盘、会计日历、抽样清单、风险路由。"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from src.audit.accounting_calendar import period_key
from src.audit.cutoff_risk_router import route_cutoff_risk
from src.audit.sample_population import build_sample_population, chain_in_population
from src.rules.cutoff_checker import CutoffChecker
from src.workflow.job_persist import load_job_state, persist_enabled
from src.workflow.job_store import JOB_STORE


def test_pytest_default_does_not_persist_to_prod_root():
    assert os.getenv("AUDIT_JOB_PERSIST") == "0"
    root = Path(os.getenv("CUTOFF_JOB_ROOT") or "")
    assert "pytest_cutoff_jobs" in root.name or "pytest_cutoff_jobs" in str(root)


def test_pytest_blocks_persist_even_if_flag_on(monkeypatch):
    monkeypatch.setenv("AUDIT_JOB_PERSIST", "1")
    monkeypatch.setenv("CUTOFF_JOB_ROOT", "D:/Dev/Temp/cutoff_jobs")
    assert persist_enabled() is False


def test_job_persist_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_JOB_PERSIST", "1")
    monkeypatch.setenv("CUTOFF_JOB_ROOT", str(tmp_path))
    assert persist_enabled() is True
    job = JOB_STORE.create(title="persist-test")
    jid = job["job_id"]
    JOB_STORE.update(jid, period_end="2025-12-31", entity_name="UT")
    # 模拟重启：清空内存
    JOB_STORE._jobs.clear()
    loaded = JOB_STORE.get(jid)
    assert loaded is not None
    assert loaded.get("period_end") == "2025-12-31"
    assert loaded.get("entity_name") == "UT"
    assert load_job_state(jid) is not None


def test_fiscal_445_period_key():
    # 财年 2025-01-01 起：第 0–3 周=P01，4–7=P02，8–12=P03
    assert period_key(
        date(2025, 1, 5), mode="fiscal_445", fiscal_year_start=date(2025, 1, 1)
    ) == "FY2025-P01"
    assert period_key(
        date(2025, 2, 10), mode="fiscal_445", fiscal_year_start=date(2025, 1, 1)
    ).startswith("FY2025-P")


def test_cutoff_checker_uses_fiscal_calendar():
    r = CutoffChecker().check(
        None,
        "2025-01-05",
        "2025-02-10",
        period_end="2025-12-31",
        calendar_mode="fiscal_445",
        fiscal_year_start="2025-01-01",
    )
    # 不同 4-4-5 期间 → FAIL
    assert r.test_status == "FAIL"


def test_sample_population_and_coverage():
    from src.audit.coverage_map import build_coverage_map

    pop = build_sample_population(business_ids=["SO25-0281", "HT25-0281"])
    assert pop["count"] == 2
    assert chain_in_population("SO25-0281", pop) is True
    assert chain_in_population("SO25-9999", pop) is False
    cov = build_coverage_map(sample_population=pop)
    dim = next(d for d in cov["dimensions"] if d["dimension_id"] == "POPULATION_COMPLETENESS")
    assert dim["status"] == "PARTIAL"


def test_cutoff_risk_router_early():
    r = route_cutoff_risk(
        test_status="FAIL", deviation_days=-10, early_recognition=True
    )
    assert r["direction"] == "early"
    assert r["risk_level"] in {"medium", "high"}
    assert r["recommended_actions"]

"""进程内测试 /api/v1/cutoff 的业务结果。

HITL 门禁由 test_hitl_gate_formal.py 独立覆盖；本文件只验证截止测试路由。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import hitl_gate
from src.api.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(hitl_gate.settings, "REQUIRE_FIELDS_CONFIRMED_API", "0")
    return TestClient(app)


def _post(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/cutoff", json=payload)
    assert response.status_code == 200, response.json()
    return response.json()


def test_pass(client: TestClient) -> None:
    data = _post(
        client,
        {
            "业务编号": "SO-001",
            "签收日期": "2026-06-01",
            "入账日期": "2026-06-01",
            "入账金额": 500,
            "合同账期天数": 10,
        },
    )
    assert data["测试状态"] == "PASS"
    assert data["应确认日期"] == "2026-06-01"


def test_fail_early(client: TestClient) -> None:
    data = _post(
        client,
        {
            "业务编号": "SO-002",
            "签收日期": "2026-01-02",
            "入账日期": "2025-12-10",
            "入账金额": 500,
            "合同账期天数": 30,
        },
    )
    assert data["测试状态"] == "FAIL"
    assert data["应确认日期"] == "2026-01-02"


def test_same_period_delayed_is_pass(client: TestClient) -> None:
    data = _post(
        client,
        {
            "业务编号": "SO-003",
            "签收日期": "2026-06-01",
            "入账日期": "2026-06-11",
            "入账金额": 500,
        },
    )
    assert data["测试状态"] == "PASS"
    assert data["应确认日期"] == "2026-06-01"

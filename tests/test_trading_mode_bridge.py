"""贸易模式桥 + GOSPD E13/P 列纪律。"""

from __future__ import annotations

import os

from src.reporting.gospd01030_filler import _normalize_transport
from src.trading_model.gospd01030 import TERM_FOB, project_gospd01030
from src.trading_model.workbook import project_workbook
from src.workflow.trading_mode_bridge import (
    control_date_for_cutoff,
    e13_from_trading_mode,
    interpret_chain_trading_mode,
    prefers_on_board_cutoff,
)


def test_gospd_extract_bl_no_from_fields():
    from src.trading_model.gospd01030 import _extract_doc_no

    assert (
        _extract_doc_no(
            [
                {
                    "doc_type": "receipt",
                    "file_name": "海运提单.pdf",
                    "fields": {"documentNo": "BL26-0285"},
                    "raw_text": "",
                }
            ]
        )
        == "BL26-0285"
    )
    allowed = [
        "客户自提",
        "签收确认",
        "验收确认",
        "外销-FOB离岸价格",
        "外销-CIF成本加保险费、运费",
    ]
    assert _normalize_transport("", allowed) == ""
    assert _normalize_transport(None, allowed) == ""
    assert _normalize_transport("外销-FOB离岸价格", allowed) == "外销-FOB离岸价格"
    # DAP 合同原文须落到「签收确认」，不能写空格
    assert (
        _normalize_transport("运输／贸易条款为 DAP 买方广州番禺仓库（Incoterms 2020）", allowed)
        == "签收确认"
    )
    assert _normalize_transport("DAP买方仓库", allowed) == "签收确认"


def test_workbook_projection_only_three_keys():
    view = project_workbook(
        {
            "status": "insufficient_evidence",
            "confidence": "low",
            "can_conclude": False,
            "nominal_incoterm": {"code": "FOB"},
        }
    )
    assert set(view.keys()) == {
        "trading_mode_conclusion",
        "status",
        "confidence",
    }
    assert "无法判断" in view["trading_mode_conclusion"]
    # 禁止把合同 FOB 标签直接当底稿结论
    assert view["trading_mode_conclusion"] != "FOB"


def test_gospd_export_uses_on_board_not_warehouse_receipt():
    cells = project_gospd01030(
        classification={
            "status": "standard_consistent",
            "confidence": "medium",
            "can_conclude": True,
            "nominal_incoterm": {"code": "FOB", "named_place_or_port": "Shanghai"},
            "actual_scenario": "FOB Shanghai",
        },
        control={
            "candidate_event": "at_on_board",
            "candidate_date": "2025-06-15",
            "result": "supported",
        },
        documents=[
            {
                "doc_type": "bill_of_lading",
                "raw_text": "B/L No. BL12345 Shipped on board",
            }
        ],
    )
    assert cells["E13_transport_terms"] == TERM_FOB
    assert cells["P_control_date"] == "2025-06-15"
    assert "On Board" in (cells["P_date_meaning"] or "")
    assert "仓库签收" in (cells["C23_cutoff_period_note"] or "")


def test_bridge_fob_on_board_cutoff(monkeypatch):
    monkeypatch.setenv("TRADING_MODEL_LIVE_LLM", "0")
    monkeypatch.setenv("CONTRACT_RAG_EMBEDDER", "hash")
    docs = [
        {
            "document_id": "c1",
            "doc_type": "contract",
            "file_name": "c.pdf",
            "raw_text": "销售合同 贸易术语 FOB 上海。买方订舱并支付海运费。风险在装船时转移。",
            "fields": {"transportTerms": "FOB Shanghai"},
        },
        {
            "document_id": "b1",
            "doc_type": "bill_of_lading",
            "file_name": "bl.pdf",
            "raw_text": "海运提单 Shipped on board 2025-06-15. B/L No. BL998877.",
            "fields": {},
        },
    ]
    out = interpret_chain_trading_mode(
        docs, transaction_id="ut-fob", use_llm=False, persist=False
    )
    assert out.get("empty") is False
    assert set((out.get("workbook_view") or {}).keys()) <= {
        "trading_mode_conclusion",
        "status",
        "confidence",
    }
    assert e13_from_trading_mode(out) == TERM_FOB
    assert prefers_on_board_cutoff(out)
    date, meaning = control_date_for_cutoff(out)
    assert date == "2025-06-15"
    assert "On Board" in meaning or "装船" in meaning


def test_bridge_ingest_and_llm_mutex(monkeypatch):
    monkeypatch.setenv("TRADING_MODEL_LIVE_LLM", "0")
    from src.trading_model.interpret import interpret_trading_model
    import pytest

    with pytest.raises(ValueError):
        interpret_trading_model(
            classified=[{"doc_type": "contract", "raw_text": "FOB"}],
            use_llm=True,
            ingest=True,
            persist=False,
        )

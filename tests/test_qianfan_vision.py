from __future__ import annotations

import base64

from src.llm import qianfan_vision


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"review_status":"RECOMMENDED",'
                            '"recommended_candidate_id":"C1",'
                            '"reason":"价税合计标签位于汇总区",'
                            '"evidence_candidate_ids":["C1"],'
                            '"missing_information":[],"confidence":0.93}'
                        )
                    }
                }
            ]
        }


def _configured_status() -> dict:
    return {
        "enabled": True,
        "configured": True,
        "model": "ernie-4.5-8k-preview",
        "api_url": "https://example.test/v2/chat/completions",
        "prompt_version": qianfan_vision.PROMPT_VERSION,
    }


def test_vision_review_sends_data_url_and_keeps_advisory_candidate(monkeypatch):
    captured: dict = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(qianfan_vision, "vision_status", _configured_status)
    monkeypatch.setattr(qianfan_vision, "_api_key", lambda: "bce-v3/test")
    monkeypatch.setattr(qianfan_vision.requests, "post", fake_post)

    result = qianfan_vision.review_amount_candidates(
        image_png=b"png-bytes",
        field_key="totalAmount",
        candidates=[
            {"candidate_id": "C1", "value": "5650.00", "label": "价税合计"},
            {"candidate_id": "C2", "value": "5700.00", "label": "总额"},
        ],
        ocr_text="价税合计 5650.00",
    )

    assert result["review_status"] == "RECOMMENDED"
    assert result["recommended_candidate_id"] == "C1"
    content = captured["json"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].endswith(base64.b64encode(b"png-bytes").decode())
    assert "MUST NOT invent a new amount" in content[0]["text"]


def test_vision_review_rejects_model_candidate_not_supplied(monkeypatch):
    class _BadResponse(_Response):
        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"review_status":"RECOMMENDED",'
                                '"recommended_candidate_id":"INVENTED",'
                                '"evidence_candidate_ids":["INVENTED"],'
                                '"confidence":1}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(qianfan_vision, "vision_status", _configured_status)
    monkeypatch.setattr(qianfan_vision, "_api_key", lambda: "bce-v3/test")
    monkeypatch.setattr(qianfan_vision.requests, "post", lambda *args, **kwargs: _BadResponse())

    result = qianfan_vision.review_amount_candidates(
        image_png=b"png-bytes",
        field_key="totalAmount",
        candidates=[{"candidate_id": "C1", "value": "5650.00"}],
    )

    assert result["review_status"] == "NEEDS_REVIEW"
    assert result["recommended_candidate_id"] is None
    assert result["evidence_candidate_ids"] == []


def test_vision_review_rejects_negative_amount(monkeypatch):
    class _NegResponse(_Response):
        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"review_status":"RECOMMENDED",'
                                '"recommended_candidate_id":"C1",'
                                '"reason":"-01357 是物料编码",'
                                '"evidence_candidate_ids":["C1"],'
                                '"confidence":0.9}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(qianfan_vision, "vision_status", _configured_status)
    monkeypatch.setattr(qianfan_vision, "_api_key", lambda: "bce-v3/test")
    monkeypatch.setattr(qianfan_vision.requests, "post", lambda *args, **kwargs: _NegResponse())

    result = qianfan_vision.review_amount_candidates(
        image_png=b"png-bytes",
        field_key="amount",
        candidates=[
            {"candidate_id": "C1", "value": -1357, "label": "不含税金额"},
            {"candidate_id": "C2", "value": 62274.76, "label": "字段·金额"},
        ],
        ocr_text="不含税金额 -01357 62274.76",
    )
    assert result["recommended_candidate_id"] is None
    assert result["review_status"] == "NEEDS_REVIEW"

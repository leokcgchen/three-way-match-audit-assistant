"""批测 LLM 辅助：无网络单测。"""

from __future__ import annotations

import os

from src.llm.batch_assist import batch_llm_assist_enabled, excerpt_in_text


def test_excerpt_in_text_allows_whitespace() -> None:
    full = "货到后 及时结清 货款"
    assert excerpt_in_text("及时结清 货款", full)
    assert not excerpt_in_text("编造的句子不在原文", full)
    assert not excerpt_in_text("短", full)


def test_batch_llm_assist_env_off(monkeypatch) -> None:
    monkeypatch.setenv("BATCH_LLM_ASSIST", "0")
    assert batch_llm_assist_enabled() is False
    monkeypatch.setenv("BATCH_LLM_ASSIST", "off")
    assert batch_llm_assist_enabled() is False

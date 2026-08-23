"""提示词目录可生成且与 prompts 版本一致。"""

from src.llm.prompt_catalog import catalog_summary, list_prompt_entries, render_sample_user
from src.llm.prompts import PROMPT_VERSION, UNIFIED_SYSTEM_PROMPT


def test_catalog_has_wired_tasks_and_system_prompt():
    summary = catalog_summary()
    assert summary["prompt_version"] == PROMPT_VERSION
    assert summary["system_prompt"] == UNIFIED_SYSTEM_PROMPT
    assert summary["wired_count"] >= 6
    entries = list_prompt_entries()
    types = {e["task_type"] for e in entries if e["wired"]}
    assert "FIELD_GAP_FILL" in types
    assert "MATCHING_DISAMBIGUATION" in types
    assert "CONCLUSION_INTERPRETATION" in types


def test_sample_user_contains_task_type():
    entry = next(e for e in list_prompt_entries() if e["task_type"] == "AMOUNT_GAP_FILL")
    text = render_sample_user(entry) or ""
    assert "AMOUNT_GAP_FILL" in text
    assert "task_type" in text

from src.workflow.sample_desk import desk_light_summary, desk_progress_breakdown


def test_desk_progress_mutually_exclusive():
    rows = [
        {"chain_id": "A", "light": "green", "reason": "ok"},
        {"chain_id": "B", "light": "red", "reason": "missing_docs"},
        {"chain_id": "C", "light": "red", "reason": "fields_gap"},
        {"chain_id": "D", "light": "yellow", "reason": "docs_uncertain"},
        {"chain_id": "E", "light": "red", "reason": "test_fail", "label": "数量不一致"},
        {"chain_id": "F", "light": "wait", "reason": "tests_pending"},
        {
            "chain_id": "G",
            "light": "red",
            "reason": "fail_closed",
            "label": "测试未通过 · 已人工确认",
        },
    ]
    p = desk_progress_breakdown(rows)
    assert p["sample_total"] == 7
    assert p["done"] == 2  # ok + fail_closed
    assert p["fail_confirmed"] == 1
    assert p["docs_missing"] == 1
    assert p["fields_missing"] == 1
    assert p["await_human"] == 1
    assert p["match_exception"] == 1
    assert p["in_progress"] == 1
    assert (
        p["done"]
        + p["docs_missing"]
        + p["fields_missing"]
        + p["match_exception"]
        + p["await_human"]
        + p["in_progress"]
        == 7
    )
    # fail_confirmed 叠在 done 上，不另占样本槽
    assert p["fail_confirmed"] <= p["done"]

    lights = desk_light_summary(rows)
    assert lights["progress"]["done"] == 2
    assert lights["progress"]["fail_confirmed"] == 1
    assert lights["legend"]["green"]
    assert lights["red"] == 4

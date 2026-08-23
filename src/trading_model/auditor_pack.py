from __future__ import annotations

from typing import Any


def build_auditor_pack(
    classified: list[dict[str, Any]],
    harvest: dict[str, Any],
    classification: dict[str, Any],
    control: dict[str, Any],
    missing: list[str],
) -> dict[str, Any]:
    originals = []
    for doc in classified:
        text = str(doc.get("raw_text") or "")
        if text.strip():
            originals.append(
                {
                    "document_id": doc.get("document_id"),
                    "page": 1,
                    "verbatim": text[:500],
                }
            )
    clarifications = [
        "Freight Prepaid/Collect 只是承运人结算状态，不等于买卖双方最终经济负担。",
        "包装费/装箱费/清洗费须先拆发生地点与最终负担，不能单独否定 FOB。",
        "POD/仓库签收不等于最终验收；received for shipment 不等于 shipped on board。",
    ]
    nom = classification.get("nominal_incoterm") or {}
    profile = classification.get("actual_fulfillment_profile") or {}
    contrasts = [
        {
            "axis": "名义 vs 实际",
            "left": nom.get("code"),
            "right": profile.get("delivery_event"),
        },
        {
            "axis": "合同义务 vs 实付 vs 最终负担",
            "left": profile.get("main_carriage_arranger"),
            "right": f"{profile.get('main_carriage_actual_payer')} / {profile.get('main_carriage_economic_burden')}",
        },
        {
            "axis": "POD vs 最终验收",
            "left": control.get("indicators", {}).get("physical_possession", {}).get("assessment"),
            "right": control.get("indicators", {}).get("customer_acceptance", {}).get("assessment"),
        },
        {
            "axis": "收货待运 vs 已装船",
            "left": profile.get("delivery_event"),
            "right": control.get("candidate_event"),
        },
    ]
    return {
        "originals": originals,
        "clarifications": clarifications,
        "contrasts": contrasts,
        "gaps": list(missing),
    }

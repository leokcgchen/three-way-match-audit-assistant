from __future__ import annotations

import re
from typing import Any

from .constants import EVENT_TO_CANDIDATE


def _text(classified: list[dict[str, Any]]) -> str:
    return "\n".join(str(d.get("raw_text") or "") for d in classified)


def assess_control(
    classified: list[dict[str, Any]],
    date_inventory: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    blob = _text(classified)
    missing: list[str] = []
    questions: list[str] = []

    if re.search(r"最终验收|performance acceptance", blob) and re.search(
        r"only after|control transfer only|控制权", blob, re.I
    ):
        candidate_event = "at_final_acceptance"
    elif re.search(r"\bDAP\b", blob, re.I) and re.search(r"ready for unloading|可供卸货", blob, re.I):
        candidate_event = "at_destination_arrival"
    elif re.search(r"loaded on board|risk (transfers )?on board|装上船|装船时", blob, re.I):
        candidate_event = "at_on_board"
    elif re.search(r"received for shipment|货交承运人", blob, re.I):
        candidate_event = "at_carrier_handover"
    else:
        candidate_event = "unresolved"

    candidate_date = None
    result = "unresolved"
    date_ids: list[int] = []
    if candidate_event == "unresolved":
        missing.append("缺少控制权候选事件日期证据")
    else:
        wanted = EVENT_TO_CANDIDATE[candidate_event]
        matches = [
            (i, row)
            for i, row in enumerate(date_inventory)
            if row.get("control_transfer_eligible") and row.get("event_type") in wanted
        ]
        if not matches:
            gap_name = wanted[0]
            missing.append(f"缺少{gap_name}事件日期证据")
        elif len(matches) > 1:
            dated = [(i, r) for i, r in matches if r.get("date")]
            if len({r["date"] for _, r in dated}) > 1:
                questions.append(
                    "控制权候选日期多行冲突: "
                    + "; ".join(f"{r['event_type']}={r.get('date')} {r.get('verbatim_excerpt')}" for _, r in matches)
                )
            elif dated:
                i, row = dated[0]
                candidate_date = row.get("date")
                date_ids = [i]
                result = "supported" if candidate_date else "unresolved"
            else:
                missing.append(f"缺少{wanted[0]}事件日期证据")
        else:
            i, row = matches[0]
            candidate_date = row.get("date")
            date_ids = [i]
            if not candidate_date:
                missing.append(f"缺少{wanted[0]}事件日期证据")
                result = "unresolved"
            else:
                result = "supported"

    pod_only = bool(re.search(r"签收|POD|Warehouse signed|收货", blob, re.I))
    needs_final = bool(re.search(r"最终验收|performance acceptance", blob, re.I))
    has_final_row = any(r["event_type"] == "final_acceptance" and r.get("date") for r in date_inventory)

    def _ind(name: str, assessment: str, excerpt: str) -> dict[str, Any]:
        return {"assessment": assessment, "evidence": [{"document_id": "", "excerpt": excerpt}] if excerpt else []}

    indicators = {
        "current_right_to_payment": _ind("pay", "unknown", ""),
        "legal_title": _ind("title", "unknown", ""),
        "physical_possession": _ind(
            "pos",
            "supported" if pod_only else "unknown",
            "签收/POD" if pod_only else "",
        ),
        "risks_and_rewards": _ind(
            "risk",
            "supported" if re.search(r"risk.*on board|装船时", blob, re.I) else "unknown",
            "risk on board" if re.search(r"on board", blob, re.I) else "",
        ),
        "customer_acceptance": _ind(
            "acc",
            "against" if needs_final and not has_final_row else ("supported" if has_final_row else "unknown"),
            "最终验收条件" if needs_final else "",
        ),
    }
    if needs_final and not has_final_row:
        result = "unresolved"

    timing = "unresolved"
    if result == "supported" and candidate_event in {"at_on_board", "at_carrier_handover", "before_carriage"}:
        timing = "after_control_transfer"
    elif result == "supported" and candidate_event in {"at_destination_arrival", "at_customer_receipt", "at_final_acceptance"}:
        timing = "before_control_transfer"

    assessment = {
        "candidate_event": candidate_event,
        "candidate_date": candidate_date,
        "result": result,
        "indicators": indicators,
        "transport_service_timing": timing,
        "date_evidence_ids": date_ids,
    }
    return assessment, missing, questions

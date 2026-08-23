from __future__ import annotations

import re
from typing import Any


def _blob(classified: list[dict[str, Any]]) -> str:
    return "\n".join(str(d.get("raw_text") or "") for d in classified)


def facts_from_text(classified: list[dict[str, Any]]) -> dict[str, Any]:
    blob = _blob(classified)
    has_recharge = bool(
        re.search(r"重收费|debit note|recharge(?:d|s)? to|recharge 100", blob, re.I)
    ) and not re.search(r"\bno recharge\b", blob, re.I)

    arranger = "unknown"
    if re.search(r"buyer nominates|买方指定", blob, re.I):
        arranger = "buyer"
    elif re.search(
        r"seller arranges|卖方订舱|seller shall insure|seller pays ocean|seller arranges and finally",
        blob,
        re.I,
    ):
        arranger = "seller"

    actual_payer = "unknown"
    burden = "unknown"
    if has_recharge:
        burden = "buyer"
        actual_payer = "seller"
    elif re.search(r"buyer(?: nominates the vessel and)? pays ocean|Freight Collect|买方承担海运", blob, re.I):
        burden = "buyer"
        actual_payer = "buyer"
    elif re.search(
        r"seller pays ocean|finally bears ocean|seller pays freight|Freight Prepaid|卖方承担海运",
        blob,
        re.I,
    ):
        burden = "seller"
        actual_payer = "seller"

    if re.search(r"Buyer nominates the vessel and pays ocean", blob, re.I):
        arranger = "buyer"
        burden = "buyer"
        actual_payer = "buyer"

    insurance = "unknown"
    if re.search(r"insured:\s*seller only|beneficiary:\s*seller|seller only", blob, re.I):
        insurance = "seller_for_self"
    elif re.search(r"for buyer's benefit|insured:\s*buyer|beneficiary:\s*buyer|claim payable to buyer", blob, re.I):
        insurance = "seller_for_buyer"
    elif re.search(r"\bCIF\b|\bCIP\b", blob) and insurance == "unknown":
        insurance = "unknown"

    delivery_event = None
    if re.search(r"not unloaded|remain on truck|未卸货", blob, re.I):
        delivery_event = "arrived_ready_for_unload"
    elif re.search(r"shipped on board|loaded on board|已装船", blob, re.I):
        delivery_event = "on_board"
    elif re.search(r"received for shipment|container terminal", blob, re.I):
        delivery_event = "carrier_received"

    risk_event = None
    if re.search(r"risk transfers when goods are loaded on board|risk on board|risk transfers on board|装船时", blob, re.I):
        risk_event = "on_board"

    return {
        "main_carriage_arranger": arranger,
        "main_carriage_actual_payer": actual_payer,
        "main_carriage_economic_burden": burden,
        "insurance_profile": insurance,
        "delivery_event": delivery_event,
        "risk_event": risk_event,
        "has_recharge": has_recharge,
        "origin_fees_only": bool(re.search(r"包装费|装箱费|清洗费|packing", blob, re.I)),
        "container_no_onboard": bool(
            re.search(r"received for shipment|container terminal|\bCY\b", blob, re.I)
        )
        and not re.search(r"shipped on board|已装船", blob, re.I),
        "sea_hint": bool(re.search(r"vessel|ocean|shipped on board|海运|装船", blob, re.I)),
        "no_place_no_version": False,
    }


def classify_trade_mode(
    harvest: dict[str, Any],
    slots: list[dict[str, Any]],
    facts: dict[str, Any],
    classified: list[dict[str, Any]],
) -> dict[str, Any]:
    unreliable = any(s["field_key"] == "incoterms" and s["availability"] == "UNRELIABLE" for s in slots)
    code = None if unreliable else harvest.get("nominal_code")
    place = harvest.get("named_place_or_port")
    version = harvest.get("version")
    evidence = [s["verbatim_excerpt"] for s in harvest.get("spans") or [] if s.get("topic") == "delivery"][:3]

    nominal = {
        "code": code,
        "named_place_or_port": place,
        "version": version,
        "evidence": evidence,
    }

    profile = {
        "delivery_event": facts.get("delivery_event"),
        "risk_event": facts.get("risk_event"),
        "main_carriage_arranger": facts.get("main_carriage_arranger"),
        "main_carriage_contractual_bearer": facts.get("main_carriage_arranger"),
        "main_carriage_actual_payer": facts.get("main_carriage_actual_payer"),
        "main_carriage_economic_burden": facts.get("main_carriage_economic_burden"),
        "insurance_profile": facts.get("insurance_profile"),
        "export_formality_party": "unknown",
        "import_formality_party": "unknown",
        "loading_party": "unknown",
        "unloading_party": "unknown",
        "cost_analysis": [],
    }

    status = "insufficient_evidence"
    candidate_profile = None
    confidence = "low"

    if not code:
        status = "insufficient_evidence"
        confidence = "low"
    elif not place and not version:
        status = "insufficient_evidence"
        confidence = "no_conclusion"
    elif code in {"FOB", "CFR", "CIF"} and facts.get("container_no_onboard"):
        status = "insufficient_evidence"
        candidate_profile = "FCA_or_nonstandard_FOB"
        confidence = "low"
    elif code == "CIF" and facts.get("insurance_profile") == "seller_for_self":
        status = "insufficient_evidence"
        confidence = "low"
    elif code == "FOB" and facts.get("main_carriage_economic_burden") == "seller" and facts.get(
        "insurance_profile"
    ) == "seller_for_buyer" and not facts.get("has_recharge"):
        status = "major_conflict"
        candidate_profile = "CIF-like"
        confidence = "low"
    elif code == "FOB" and facts.get("has_recharge") and facts.get("main_carriage_actual_payer") == "seller":
        status = "standard_modified"
        confidence = "medium"
    elif code == "FOB" and facts.get("main_carriage_arranger") == "buyer" and facts.get(
        "main_carriage_economic_burden"
    ) in {"buyer", "unknown"}:
        if facts.get("insurance_profile") == "seller_for_buyer" and not facts.get("has_recharge"):
            status = "major_conflict"
            candidate_profile = "CIF-like"
            confidence = "low"
        else:
            status = "standard_consistent"
            confidence = "medium"
    elif code == "CFR" and facts.get("main_carriage_economic_burden") == "seller" and facts.get("risk_event") == "on_board":
        status = "standard_consistent"
        confidence = "medium"
    elif code == "DAP":
        status = "standard_consistent"
        confidence = "medium"
        if facts.get("delivery_event") == "unloaded":
            candidate_profile = "DPU-like"
            status = "standard_modified"
    else:
        status = "insufficient_evidence"
        confidence = "low"

    return {
        "nominal_incoterm": nominal,
        "actual_fulfillment_profile": profile,
        "status": status,
        "candidate_profile": candidate_profile,
        "confidence": confidence,
    }

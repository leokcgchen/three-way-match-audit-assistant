from __future__ import annotations

import re
from typing import Any

from .constants import CONTROL_ELIGIBLE_EVENTS

_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "on_board",
        re.compile(r"shipped\s+on\s+board|已装船", re.I),
        "RULE_ON_BOARD: 提单标注 Shipped on board + 船名/航次 → on_board，不以签发日替代",
    ),
    (
        "carrier_received",
        re.compile(r"received\s+for\s+shipment|container\s+terminal|\bCY\b|进堆场|货交承运人", re.I),
        "RULE_CARRIER_RECEIVED: received for shipment / 堆场接收 → carrier_received，不得当作 on_board",
    ),
    (
        "final_acceptance",
        re.compile(r"最终验收|performance acceptance|验收合格", re.I),
        "RULE_FINAL_ACCEPTANCE: 合同或证书载明最终/性能验收 → final_acceptance，不以签收替代",
    ),
    (
        "customer_receipt",
        re.compile(r"\bPOD\b|客户签收|signed\s+POD|Warehouse signed|收货", re.I),
        "RULE_CUSTOMER_RECEIPT: 签收/POD/仓库收货 → customer_receipt，不等于最终验收",
    ),
    (
        "recharge_or_settlement",
        re.compile(r"重收费|debit note|recharge", re.I),
        "RULE_RECHARGE: 重收费/借贷项结算日 → recharge_or_settlement，非控制权事件",
    ),
    (
        "destination_arrived",
        re.compile(r"arrive[d]?\s+(ready\s+for\s+unloading|at buyer)|到港|到站", re.I),
        "RULE_ARRIVAL: 到达可供卸货/到港 → destination_arrived",
    ),
    (
        "unloaded",
        re.compile(r"unloaded|已卸货", re.I),
        "RULE_UNLOADED: 明确卸货完成 → unloaded",
    ),
]


def build_date_inventory(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for doc in classified:
        text = str(doc.get("raw_text") or "")
        doc_id = str(doc.get("document_id") or "")
        # skip unload negation
        lowered = text.lower()
        for event, rx, why in _RULES:
            if event == "recharge_or_settlement" and re.search(r"\bno recharge\b", text, re.I):
                if not re.search(r"重收费|debit note|recharge(?:d|s)? to", text, re.I):
                    continue
            if event == "final_acceptance" and re.search(
                r"pending quality|待验收|only after", text, re.I
            ) and "合格" not in text:
                # contract mentioning 最终验收 as future condition is not an occurred event
                if not re.search(r"验收合格|acceptance certificate", text, re.I):
                    continue
            m = rx.search(text)
            if not m:
                continue
            dm = _DATE_RE.search(text)
            date = dm.group(1) if dm else None
            rows.append(
                {
                    "event_type": event,
                    "date": date,
                    "precision": "day" if date else "unknown",
                    "document_id": doc_id,
                    "verbatim_excerpt": text[max(0, m.start() - 8) : min(len(text), m.end() + 50)].strip(),
                    "signer_or_issuer": None,
                    "source_quality": "third_party" if doc.get("doc_type") == "bill_of_lading" else "document",
                    "why_this_event": why,
                    "control_transfer_eligible": event in CONTROL_ELIGIBLE_EVENTS,
                }
            )
    return rows

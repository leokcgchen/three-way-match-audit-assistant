"""重复号 / 多版本检测（规则只读，不改测试终态）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.legacy_ocr.ledger_parser import normalize_biz_id


_FIELD_BY_TYPE = {
    "invoice": ("invoiceNo", "documentNo"),
    "contract": ("contractNo", "documentNo"),
    "order": ("orderNo", "documentNo", "salesOrderNo"),
    "payment": ("documentNo",),
    "delivery": ("documentNo", "orderNo"),
    "receipt": ("documentNo", "warehouseNo", "orderNo"),
}


def _pick_biz_id(item: Dict[str, Any], fields: Tuple[str, ...]) -> str:
    src = item.get("fields") or {}
    for key in fields:
        raw = src.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        nid = normalize_biz_id(raw)
        if nid:
            return nid
    return ""


def detect_duplicates(
    classified: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """扫描同批单据：重复票号、同合同/订单多文件版本。"""
    docs = [x for x in (classified or []) if isinstance(x, dict)]
    active = [x for x in docs if not x.get("excluded_from_match")]

    groups: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    # key = (kind, normalized_id)
    for item in active:
        doc_type = str(item.get("doc_type") or "").strip().lower()
        file_name = str(item.get("file_name") or "")
        field_names = _FIELD_BY_TYPE.get(doc_type, ("documentNo",))
        biz = _pick_biz_id(item, field_names)
        if not biz:
            continue
        kind = {
            "invoice": "invoice_no",
            "contract": "contract_no",
            "order": "order_no",
        }.get(doc_type, f"{doc_type or 'doc'}_no")
        groups[(kind, biz)].append(
            {
                "file_name": file_name,
                "doc_type": doc_type,
                "biz_id": biz,
            }
        )

    # 跨类型：发票号若出现在非发票单据字段中与发票冲突（弱信号）
    invoice_ids = {
        _pick_biz_id(x, _FIELD_BY_TYPE["invoice"])
        for x in active
        if x.get("doc_type") == "invoice"
    }
    invoice_ids.discard("")

    findings: List[Dict[str, Any]] = []
    for (kind, biz), members in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        if len(members) < 2:
            continue
        files = [m["file_name"] for m in members]
        severity = "WARNING"
        issue_type = "DUPLICATE_ID"
        title = f"重复编号 {biz}"
        if kind == "invoice_no":
            title = f"重复发票号 {biz}"
            issue_type = "DUPLICATE_INVOICE_NO"
            severity = "FAIL_SIGNAL"
        elif kind == "contract_no":
            title = f"同合同多版本/多文件 {biz}"
            issue_type = "MULTI_VERSION_CONTRACT"
        elif kind == "order_no":
            title = f"同订单多文件 {biz}"
            issue_type = "MULTI_VERSION_ORDER"
        findings.append(
            {
                "finding_id": f"{kind}:{biz}",
                "issue_type": issue_type,
                "severity": severity,
                "biz_id": biz,
                "kind": kind,
                "title": title,
                "file_names": files,
                "count": len(files),
                "note": "仅提示，不自动改规则终态；请人工确认保留哪一版或是否误传。",
            }
        )

    cross_hits: List[Dict[str, Any]] = []
    for item in active:
        if item.get("doc_type") == "invoice":
            continue
        fields = item.get("fields") or {}
        for key in ("invoiceNo", "documentNo"):
            nid = normalize_biz_id(fields.get(key))
            if nid and nid in invoice_ids and key == "invoiceNo":
                cross_hits.append(
                    {
                        "finding_id": f"cross_invoice:{nid}:{item.get('file_name')}",
                        "issue_type": "CROSS_DOC_INVOICE_REF",
                        "severity": "INFO",
                        "biz_id": nid,
                        "kind": "cross_invoice_ref",
                        "title": f"非发票单据引用发票号 {nid}",
                        "file_names": [str(item.get("file_name") or "")],
                        "count": 1,
                        "note": "信息性提示，常见于订单/回款备注。",
                    }
                )

    all_findings = findings + cross_hits
    return {
        "ran": True,
        "version": "duplicate-detector-v1",
        "summary": {
            "total": len(all_findings),
            "duplicates": len(findings),
            "fail_signals": sum(1 for f in findings if f.get("severity") == "FAIL_SIGNAL"),
            "multi_version": sum(
                1 for f in findings if str(f.get("issue_type", "")).startswith("MULTI_")
            ),
        },
        "findings": all_findings,
        "blocks_downstream_hint": any(
            f.get("severity") == "FAIL_SIGNAL" for f in findings
        ),
    }

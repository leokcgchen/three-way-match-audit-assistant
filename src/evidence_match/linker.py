"""证据匹配：按业务索引将合同/订单/发货/签收/发票/回款/序时账串联。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence, Set

from pydantic import BaseModel, Field

from src.legacy_ocr.ledger_parser import (
    collect_document_biz_keys,
    compact_biz_id,
    extract_biz_ids_from_filename,
    normalize_biz_id,
)

EvidenceStatus = Literal["PASS", "WARNING", "FAIL"]

# 证据链核心节点（审计常用）
CORE_ROLES = ("contract", "order", "delivery", "receipt", "invoice", "ledger")
# 扩展节点（有则加分，无则不强制 FAIL）
OPTIONAL_ROLES = ("payment",)

ROLE_LABELS = {
    "contract": "合同",
    "order": "订单",
    "delivery": "发货",
    "receipt": "签收/验收",
    "invoice": "发票",
    "payment": "回款",
    "ledger": "序时账",
    "other": "其他",
}


class EvidenceNode(BaseModel):
    role: str
    file_name: str = ""
    doc_type: str = ""
    biz_keys: List[str] = Field(default_factory=list)
    primary_id: Optional[str] = None
    linked: bool = False
    note: Optional[str] = None


class EvidenceLink(BaseModel):
    from_role: str
    to_role: str
    shared_keys: List[str] = Field(default_factory=list)


class EvidenceMatchResult(BaseModel):
    status: EvidenceStatus
    anchor_keys: List[str] = Field(default_factory=list)
    nodes: List[EvidenceNode] = Field(default_factory=list)
    links: List[EvidenceLink] = Field(default_factory=list)
    missing_roles: List[str] = Field(default_factory=list)
    issue_description: str = ""
    human_readable_summary: str = ""
    llm_disambiguation: Optional[Dict[str, Any]] = None


def _doc_role(doc_type: str) -> str:
    t = (doc_type or "").strip().lower()
    if t in {"contract", "order", "delivery", "receipt", "invoice", "payment"}:
        return t
    if t in {"warehouse_receipt"}:
        return "receipt"
    if t in {"purchase_order"}:
        return "order"
    return "other"


def _node_keys(item: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    fields = item.get("fields") or {}
    for k in collect_document_biz_keys(fields):
        if k not in keys:
            keys.append(k)
    for k in extract_biz_ids_from_filename(str(item.get("file_name") or "")):
        if k not in keys:
            keys.append(k)
    # 显式编号字段
    for fname in ("documentNo", "contractNo", "orderNo", "invoiceNo", "warehouseNo"):
        val = fields.get(fname)
        if val is None:
            continue
        norm = normalize_biz_id(val)
        if norm and norm not in keys:
            keys.append(norm)
    return keys


def _key_set(keys: Sequence[str]) -> Set[str]:
    out: Set[str] = set()
    for k in keys:
        n = normalize_biz_id(k)
        if n:
            out.add(n)
            c = compact_biz_id(n)
            if c:
                out.add(c)
    return out


def _overlap(a: Sequence[str], b: Sequence[str]) -> List[str]:
    sa, sb = _key_set(a), _key_set(b)
    shared_compact = {compact_biz_id(x) for x in sa} & {compact_biz_id(x) for x in sb}
    # 返回可读形式（优先带分隔符的原样）
    result: List[str] = []
    for key in a:
        if compact_biz_id(key) in shared_compact:
            nk = normalize_biz_id(key)
            if nk and nk not in result:
                result.append(nk)
    return result


def _pick_anchor(all_keys: Sequence[str]) -> List[str]:
    """优先 SO/订单号，其次 HT/合同号，再次其它。"""
    sos = [k for k in all_keys if normalize_biz_id(k).startswith("SO")]
    hts = [k for k in all_keys if normalize_biz_id(k).startswith("HT")]
    if sos:
        return list(dict.fromkeys(sos))
    if hts:
        return list(dict.fromkeys(hts))
    return list(dict.fromkeys(all_keys))[:5]


def build_evidence_chain(
    classified: Sequence[Dict[str, Any]],
    *,
    ledger_matched_biz_id: Optional[str] = None,
    ledger_posting_date: Optional[str] = None,
    require_delivery: bool = False,
    require_payment: bool = False,
) -> EvidenceMatchResult:
    """
    根据业务编号将已分类单据与序时账串联为证据链。

    - 同链：节点之间共享 SO/HT/PO 等索引（支持去分隔符模糊）
    - 核心角色默认：合同、订单、签收、发票、序时账；发货/回款可选
    """
    nodes: List[EvidenceNode] = []
    all_keys: List[str] = []

    for item in classified:
        role = _doc_role(str(item.get("doc_type") or ""))
        keys = _node_keys(item)
        for k in keys:
            if k not in all_keys:
                all_keys.append(k)
        primary = keys[0] if keys else None
        nodes.append(
            EvidenceNode(
                role=role,
                file_name=str(item.get("file_name") or ""),
                doc_type=str(item.get("doc_type") or ""),
                biz_keys=keys,
                primary_id=primary,
            )
        )

    # 序时账节点
    ledger_keys: List[str] = []
    if ledger_matched_biz_id:
        ledger_keys = [normalize_biz_id(ledger_matched_biz_id)]
        for k in ledger_keys:
            if k not in all_keys:
                all_keys.append(k)
    nodes.append(
        EvidenceNode(
            role="ledger",
            file_name="序时账",
            doc_type="ledger",
            biz_keys=ledger_keys,
            primary_id=ledger_keys[0] if ledger_keys else None,
            note=(
                f"过账日 {ledger_posting_date}"
                if ledger_posting_date
                else ("未匹配到序时账行" if not ledger_keys else None)
            ),
        )
    )

    anchors = _pick_anchor(all_keys)
    anchor_set = _key_set(anchors)

    # 并查集：共享业务编号的节点连通（含合同 HT 与订单 SO 同文件名批次）
    parent = list(range(len(nodes)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri, rj = _find(i), _find(j)
        if ri != rj:
            parent[rj] = ri

    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if j <= i:
                continue
            if a.role == "other" or b.role == "other":
                continue
            if _overlap(a.biz_keys, b.biz_keys):
                _union(i, j)
        # 文件名同时含多编号时，把该节点键并入全局锚点簇
        if len(_key_set(a.biz_keys)) >= 2 and ( _key_set(a.biz_keys) & anchor_set):
            pass

    # 扩展：文件名提取的多键（SO+HT）视为同簇——对每个节点，若其 keys 与锚点有交或与已连通节点有交
    # 再扫一遍：任何持有 HT 且同批存在「同时持有 SO+HT」的节点，并入 SO 簇
    bridge_keys = set()
    for n in nodes:
        ks = _key_set(n.biz_keys)
        has_so = any(normalize_biz_id(k).startswith("SO") for k in n.biz_keys)
        has_ht = any(normalize_biz_id(k).startswith("HT") for k in n.biz_keys)
        if has_so and has_ht:
            bridge_keys |= ks
    if bridge_keys:
        for i, n in enumerate(nodes):
            if _key_set(n.biz_keys) & bridge_keys:
                for j, m in enumerate(nodes):
                    if _key_set(m.biz_keys) & bridge_keys:
                        _union(i, j)

    # 主簇：含锚点最多的连通分量
    root_scores: Dict[int, int] = {}
    for i, n in enumerate(nodes):
        if not n.biz_keys:
            continue
        r = _find(i)
        score = len(_key_set(n.biz_keys) & anchor_set)
        root_scores[r] = root_scores.get(r, 0) + 1 + score
    main_root = max(root_scores, key=root_scores.get) if root_scores else None

    for i, node in enumerate(nodes):
        if node.role == "other":
            continue
        if node.role == "ledger" and not node.biz_keys:
            node.linked = False
            continue
        if not node.biz_keys:
            node.linked = False
            node.note = ((node.note + "；") if node.note else "") + "缺业务编号"
            continue
        node.linked = main_root is not None and _find(i) == main_root

    # 建边：任意两节点共享键
    links: List[EvidenceLink] = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            if a.role == "other" or b.role == "other":
                continue
            shared = _overlap(a.biz_keys, b.biz_keys)
            if shared:
                links.append(
                    EvidenceLink(
                        from_role=a.role,
                        to_role=b.role,
                        shared_keys=shared,
                    )
                )

    present_roles = {n.role for n in nodes if n.role != "other"}
    required_core = ["contract", "order", "receipt", "invoice", "ledger"]
    if require_delivery:
        required_core.insert(2, "delivery")
    if require_payment:
        required_core.append("payment")

    # 有签收即可；发货单独作为可选增强（除非 require_delivery）
    missing = [r for r in required_core if r not in present_roles]
    unlinked = [
        n.role
        for n in nodes
        if n.role in required_core and n.role in present_roles and not n.linked
    ]

    # 签收可替代「交付证据」；无签收但有发货时，用发货顶上 receipt 要求
    if "receipt" in missing and any(n.role == "delivery" and n.linked for n in nodes):
        missing = [r for r in missing if r != "receipt"]

    hard_missing = [
        r for r in missing if r not in ({"delivery"} if not require_delivery else set())
    ]
    soft_notes: List[str] = []
    if "delivery" not in present_roles and not require_delivery:
        soft_notes.append("未上传发货单（可选）")
    if "payment" not in present_roles and not require_payment:
        soft_notes.append("未上传回款资料（可选）")

    if hard_missing or unlinked:
        status: EvidenceStatus = "FAIL"
        parts: List[str] = []
        if hard_missing:
            parts.append(
                "缺少：" + ", ".join(ROLE_LABELS.get(r, r) for r in hard_missing)
            )
        if unlinked:
            parts.append(
                "未与锚点编号连通："
                + ", ".join(ROLE_LABELS.get(r, r) for r in dict.fromkeys(unlinked))
            )
        issue = "；".join(parts + soft_notes)
    else:
        status = "PASS"
        issue = "核心证据已按业务编号串联"
        # 发货/回款为可选：缺了也不打 WARNING，避免结论页误拦

    summary_bits = []
    if anchors:
        summary_bits.append(f"锚点编号 {', '.join(anchors[:3])}")
    linked_labels = [
        ROLE_LABELS.get(n.role, n.role)
        for n in nodes
        if n.linked and n.role != "other"
    ]
    if linked_labels:
        summary_bits.append("已串联：" + " → ".join(dict.fromkeys(linked_labels)))
    summary_bits.append(issue)
    if soft_notes and status == "PASS":
        summary_bits.append("可选附件：" + "；".join(soft_notes))
    summary = "；".join(summary_bits)

    return EvidenceMatchResult(
        status=status,
        anchor_keys=anchors,
        nodes=nodes,
        links=links,
        missing_roles=missing,
        issue_description=issue,
        human_readable_summary=summary,
    )


def heal_optional_attachment_warning(blob: dict[str, Any] | None) -> bool:
    """旧结果里「仅缺可选发货/回款」的 WARNING 升级为 PASS（规则变更后兼容落盘 job）。"""
    if not isinstance(blob, dict):
        return False
    if str(blob.get("status") or "").upper() != "WARNING":
        return False
    issue = str(blob.get("issue_description") or "")
    if "缺少：" in issue or "未与锚点" in issue:
        return False
    if "可选" not in issue and "未上传发货单" not in issue and "未上传回款" not in issue:
        return False
    blob["status"] = "PASS"
    blob["issue_description"] = "核心证据已按业务编号串联"
    summary = str(blob.get("human_readable_summary") or "")
    if summary and "WARNING" not in summary.upper():
        blob["human_readable_summary"] = summary.replace(
            "；未上传发货单（可选）", ""
        ).replace("；未上传回款资料（可选）", "").replace("未上传发货单（可选）；", "").replace(
            "未上传回款资料（可选）；", ""
        )
    return True

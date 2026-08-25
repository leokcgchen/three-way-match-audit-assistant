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
    business_group_id: Optional[str] = None
    biz_keys: List[str] = Field(default_factory=list)
    primary_id: Optional[str] = None
    linked: bool = False
    note: Optional[str] = None


class EvidenceLink(BaseModel):
    from_role: str
    to_role: str
    from_file_name: Optional[str] = None
    to_file_name: Optional[str] = None
    link_basis: str = "business_key"
    business_group_id: Optional[str] = None
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
    # 人工确认的业务分组及归类引擎候选也是可审计的业务索引。
    # 单据自身编号（SO/FP/YS）可以彼此不同，不能因此丢掉与序时账的 YW 连接。
    explicit_business_keys = [
        item.get("business_group_id"),
        item.get("sample_business_id"),
    ]
    business_ids = item.get("business_ids")
    if isinstance(business_ids, (list, tuple, set)):
        explicit_business_keys.extend(business_ids)
    elif business_ids:
        explicit_business_keys.append(business_ids)
    for value in explicit_business_keys:
        norm = normalize_biz_id(value)
        if norm and norm not in keys:
            keys.append(norm)
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


# These fields are references to another business document.  They are
# deliberately narrower than generic documentNo and own identifiers such as
# invoiceNo/warehouseNo, so heterogeneous source-document numbering does not
# become a false mismatch.
_CROSS_REFERENCE_FIELDS = {
    "订单号": ("orderNo", "salesOrderNo", "purchaseOrderNo"),
    "合同号": ("contractNo",),
}


def _manual_group_reference_conflicts(
    classified: Sequence[Dict[str, Any]],
) -> List[str]:
    """Return explicit same-semantic reference conflicts inside manual groups."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in classified:
        group_id = str(item.get("business_group_id") or "").strip()
        if group_id:
            groups.setdefault(group_id, []).append(item)

    conflicts: List[str] = []
    for group_docs in groups.values():
        for label, field_names in _CROSS_REFERENCE_FIELDS.items():
            values: List[str] = []
            for item in group_docs:
                fields = item.get("fields") or {}
                for field_name in field_names:
                    value = compact_biz_id(fields.get(field_name))
                    if value and value not in values:
                        values.append(value)
            if len(values) > 1:
                conflicts.append(f"{label}显式交叉引用冲突：{' / '.join(values)}")
    return conflicts


def build_evidence_chain(
    classified: Sequence[Dict[str, Any]],
    *,
    ledger_matched_biz_id: Optional[str] = None,
    ledger_posting_date: Optional[str] = None,
    require_contract: bool = True,
    require_ledger: bool = True,
    require_delivery: bool = False,
    require_payment: bool = False,
) -> EvidenceMatchResult:
    """
    根据业务编号将已分类单据与序时账串联为证据链。

    - 同链：节点之间共享 SO/HT/PO 等索引（支持去分隔符模糊）
    - 核心角色默认：合同、订单、签收、发票、序时账；调用方可按底稿目标关闭合同/序时账要求
    """
    nodes: List[EvidenceNode] = []
    all_keys: List[str] = []
    manual_group_nodes: Dict[str, List[int]] = {}

    for item in classified:
        role = _doc_role(str(item.get("doc_type") or ""))
        keys = _node_keys(item)
        for k in keys:
            if k not in all_keys:
                all_keys.append(k)
        primary = keys[0] if keys else None
        business_group_id = str(item.get("business_group_id") or "").strip() or None
        nodes.append(
            EvidenceNode(
                role=role,
                file_name=str(item.get("file_name") or ""),
                doc_type=str(item.get("doc_type") or ""),
                business_group_id=business_group_id,
                biz_keys=keys,
                primary_id=primary,
            )
        )
        if business_group_id and role != "other":
            manual_group_nodes.setdefault(business_group_id, []).append(len(nodes) - 1)

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

    def _can_machine_link(a: EvidenceNode, b: EvidenceNode) -> bool:
        """Human group membership is a hard boundary for machine matching."""
        a_group, b_group = a.business_group_id, b.business_group_id
        if a_group or b_group:
            return bool(a_group and b_group and a_group == b_group)
        return True

    # A business_group_id is an auditable human decision about membership.  It
    # therefore joins the business documents before machine-number matching.
    for member_indexes in manual_group_nodes.values():
        for index in member_indexes[1:]:
            _union(member_indexes[0], index)

    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if j <= i:
                continue
            if a.role == "other" or b.role == "other":
                continue
            if manual_group_nodes and (a.role == "ledger" or b.role == "ledger"):
                continue
            if _can_machine_link(a, b) and _overlap(a.biz_keys, b.biz_keys):
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
                    if (
                        not (manual_group_nodes and (n.role == "ledger" or m.role == "ledger"))
                        and _can_machine_link(n, m)
                        and _key_set(m.biz_keys) & bridge_keys
                    ):
                        _union(i, j)

    # A ledger row may enter a manual packet only when its key identifies one
    # and only one manual group.  Matching several groups is ambiguous and is
    # intentionally left unlinked rather than silently crossing the boundary.
    ledger_index = len(nodes) - 1
    matching_manual_groups = [
        indexes
        for indexes in manual_group_nodes.values()
        if any(_overlap(nodes[ledger_index].biz_keys, nodes[index].biz_keys) for index in indexes)
    ]
    if len(matching_manual_groups) == 1:
        _union(ledger_index, matching_manual_groups[0][0])

    # 主簇：含锚点最多的连通分量
    root_scores: Dict[int, int] = {}
    for i, n in enumerate(nodes):
        if not n.biz_keys:
            continue
        r = _find(i)
        score = len(_key_set(n.biz_keys) & anchor_set)
        root_scores[r] = root_scores.get(r, 0) + 1 + score
    if len(manual_group_nodes) == 1:
        main_root = _find(next(iter(manual_group_nodes.values()))[0])
    else:
        main_root = max(root_scores, key=root_scores.get) if root_scores else None

    for i, node in enumerate(nodes):
        if node.role == "other":
            continue
        if node.role == "ledger" and not node.biz_keys:
            node.linked = False
            continue
        if node.business_group_id:
            node.linked = main_root is not None and _find(i) == main_root
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
            if manual_group_nodes and (
                (a.role == "ledger" and not b.business_group_id)
                or (b.role == "ledger" and not a.business_group_id)
            ):
                continue
            manual_group_id = (
                a.business_group_id
                if a.business_group_id and a.business_group_id == b.business_group_id
                else None
            )
            if shared and not _can_machine_link(a, b):
                continue
            if shared or manual_group_id:
                links.append(
                    EvidenceLink(
                        from_role=a.role,
                        to_role=b.role,
                        from_file_name=a.file_name or None,
                        to_file_name=b.file_name or None,
                        link_basis="manual_business_group" if manual_group_id else "business_key",
                        business_group_id=manual_group_id,
                        shared_keys=shared,
                    )
                )

    present_roles = {n.role for n in nodes if n.role != "other"}
    required_core = ["order", "receipt", "invoice"]
    if require_contract:
        required_core.insert(0, "contract")
    if require_ledger:
        required_core.append("ledger")
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

    reference_conflicts = _manual_group_reference_conflicts(classified)
    manual_scope_error = len(manual_group_nodes) > 1

    if hard_missing or unlinked or reference_conflicts or manual_scope_error:
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
        parts.extend(reference_conflicts)
        if manual_scope_error:
            parts.append("当前证据链输入包含多个人工业务分组，请按组独立计算")
        issue = "；".join(parts + soft_notes)
    else:
        status = "PASS"
        issue = (
            "核心证据已按人工业务分组串联"
            if manual_group_nodes
            else "核心证据已按业务编号串联"
        )
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

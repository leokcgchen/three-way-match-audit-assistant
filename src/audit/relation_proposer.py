"""从证据链 / 消歧结果生成 PROPOSED 候选关系（不改规则终态）。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from src.models.relation_candidates import new_relation, upsert_relations


def _node_file_by_role(nodes: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        role = str(node.get("role") or "")
        fname = str(node.get("file_name") or "")
        if role and fname and role != "other":
            out[role] = fname
    return out


def propose_relations_from_evidence(
    classified: Optional[Iterable[Dict[str, Any]]],
    evidence: Optional[Dict[str, Any]],
    *,
    existing: Optional[Iterable[Dict[str, Any]]] = None,
    actor: str = "system",
) -> List[Dict[str, Any]]:
    """由 EvidenceLink + 消歧 proposals 生成/合并候选关系。"""
    evidence = evidence or {}
    nodes = evidence.get("nodes") or []
    links = evidence.get("links") or []
    role_file = _node_file_by_role(nodes)
    candidates: List[Dict[str, Any]] = []

    for link in links:
        if not isinstance(link, dict):
            continue
        fr = str(link.get("from_role") or "")
        tr = str(link.get("to_role") or "")
        from_id = role_file.get(fr, fr)
        to_id = role_file.get(tr, tr)
        if not from_id or not to_id or from_id == to_id:
            continue
        keys = [str(k) for k in (link.get("shared_keys") or []) if k]
        excerpt = f"共享业务编号：{', '.join(keys[:4])}" if keys else "证据链角色串联"
        candidates.append(
            new_relation(
                from_id=from_id,
                to_id=to_id,
                rel_type=f"LINKED_{fr}_TO_{tr}".upper(),
                status="PROPOSED",
                source_doc=from_id,
                excerpt=excerpt,
                actor=actor,
                shared_keys=keys,
                note="来自证据匹配规则链",
                extra={"from_role": fr, "to_role": tr, "source": "evidence_link"},
            )
        )

    disamb = evidence.get("llm_disambiguation") or {}
    for prop in disamb.get("proposals") or []:
        if not isinstance(prop, dict):
            continue
        fname = str(prop.get("file_name") or "")
        disposition = str(prop.get("disposition") or "").upper()
        if not fname:
            continue
        anchor = ""
        for node in nodes:
            if node.get("linked") and node.get("file_name"):
                anchor = str(node.get("file_name"))
                break
        if not anchor:
            for item in classified or []:
                if item.get("doc_type") == "invoice":
                    anchor = str(item.get("file_name") or "")
                    break
        to_id = anchor or "(cluster)"
        if disposition == "EXCLUDE":
            rel_type = "EXCLUDE_FROM_CLUSTER"
        elif disposition == "ADOPT":
            rel_type = "ADOPT_INTO_CLUSTER"
        else:
            rel_type = "KEEP_CANDIDATE"
        candidates.append(
            new_relation(
                from_id=fname,
                to_id=to_id,
                rel_type=rel_type,
                status="PROPOSED",
                source_doc=fname,
                excerpt=str(prop.get("excerpt") or "")[:240],
                actor="llm_matching",
                shared_keys=[str(prop.get("suggested_biz_id") or "")]
                if prop.get("suggested_biz_id")
                else [],
                note=str(prop.get("reason") or disposition),
                extra={"disposition": disposition, "source": "matching_disambiguation"},
            )
        )

    return upsert_relations(existing, candidates, preserve_decided=True)

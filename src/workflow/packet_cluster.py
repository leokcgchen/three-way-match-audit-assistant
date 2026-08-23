"""单据单元 → 业务笔：强主键 union；禁止金额/日期静默并笔。"""

from __future__ import annotations

from typing import Any

from src.reporting.gospd01010_filler import group_classified_by_chain
from src.workflow.packet_cards import UNRESOLVED

UNIDENTIFIED_CHAIN = "未识别业务号"


def _strong_ids(keys: dict[str, Any], extra: list[str] | None = None) -> list[str]:
    from src.legacy_ocr.ledger_parser import normalize_biz_id

    out: list[str] = []
    for raw in list((keys or {}).values()) + list(extra or []):
        nid = normalize_biz_id(raw) if raw else ""
        if not nid:
            continue
        u = nid.upper()
        if u.startswith(("SO", "PO")) or "HT" in u or u.startswith(("CT", "CONTRACT")):
            if nid not in out:
                out.append(nid)
    return out


def cluster_units(
    units: list[dict[str, Any]],
    *,
    file_kinds: dict[str, str] | None = None,
    file_modes: dict[str, str] | None = None,
    filename_ids: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """为每个单元写入 chain_id。

    file_kinds: source_file → standard|packet_single_chain|packet_multi_chain
    file_modes: 人工声明 single|multi（覆盖自动判定）
    情况 A 整包默认一笔；包内多个互异 SO 则降级为情况 B 并告警。
    """
    file_kinds = file_kinds or {}
    file_modes = file_modes or {}
    filename_ids = filename_ids or {}
    warnings: list[str] = []
    out = [dict(u) for u in units]
    by_file: dict[str, list[dict[str, Any]]] = {}
    for item in out:
        by_file.setdefault(str(item.get("source_file") or ""), []).append(item)

    for source, group in by_file.items():
        kind = str(file_kinds.get(source) or "packet_single_chain")
        mode = str(file_modes.get(source) or "").strip().lower()
        file_strong: list[str] = []
        for item in group:
            file_strong.extend(_strong_ids(dict(item.get("keys") or {})))
        distinct = list(dict.fromkeys(file_strong))
        so_ids = [x for x in distinct if str(x).upper().startswith(("SO", "PO"))]
        ht_ids = [
            x
            for x in distinct
            if "HT" in str(x).upper() or str(x).upper().startswith(("CT", "CONTRACT"))
        ]
        if mode == "multi":
            kind = "packet_multi_chain"
        elif mode == "single":
            kind = "packet_single_chain"

        if kind == "packet_single_chain":
            if len(so_ids) > 1:
                warnings.append(
                    f"{source}：一文件一笔包内抽出多个订单号 {so_ids}，已按多笔处理"
                )
                kind = "packet_multi_chain"
            else:
                chain = ""
                if so_ids:
                    chain = so_ids[0]
                elif ht_ids:
                    chain = ht_ids[0]
                else:
                    fname_ids = list(filename_ids.get(source) or [])
                    from src.legacy_ocr.ledger_parser import normalize_biz_id

                    so = next(
                        (
                            normalize_biz_id(x)
                            for x in fname_ids
                            if str(x).upper().startswith("SO")
                        ),
                        "",
                    )
                    chain = so or (
                        normalize_biz_id(fname_ids[0]) if fname_ids else UNIDENTIFIED_CHAIN
                    )
                for item in group:
                    item["chain_id"] = chain
                    item["cluster_mode"] = "packet_single_chain"
                continue

        # 情况 B / 降级后的多笔：强主键 union，无强号进未识别
        pseudo: list[dict[str, Any]] = []
        for idx, item in enumerate(group):
            fields = dict(item.get("keys") or {})
            host = str(item.get("doc_type") or item.get("host_type") or "other")
            if host == UNRESOLVED:
                host = "other"
            pseudo.append(
                {
                    "_idx": idx,
                    "file_name": f"{source}::u{idx}",
                    "doc_type": host,
                    "fields": fields,
                }
            )
        grouped = group_classified_by_chain(
            pseudo,
            allow_weak_unique_attach=False,
            allow_unique_so_ht_merge=False,
        )
        assigned: set[int] = set()
        for chain_id, docs in grouped:
            cid = chain_id or UNIDENTIFIED_CHAIN
            for doc in docs:
                idx = int(doc.get("_idx") or 0)
                group[idx]["chain_id"] = cid
                group[idx]["cluster_mode"] = "packet_multi_chain"
                assigned.add(idx)
        for idx, item in enumerate(group):
            if idx not in assigned:
                item["chain_id"] = UNIDENTIFIED_CHAIN
                item["cluster_mode"] = "packet_multi_chain"

    return out, warnings

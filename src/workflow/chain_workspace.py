"""GOSPD 分笔业务：同任务累加单据，按业务链独立测试，导出汇总同一份底稿。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from src.reporting.gospd01010_filler import group_classified_by_chain
from src.workflow.recipes import STEP_AMOUNT, STEP_CONTRACT, STEP_THREE_WAY


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_gospd_mode(job: dict[str, Any]) -> bool:
    goals = list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or [])
    return bool(
        {"gospd01010", "gospd01030", "gospd01010_2", "gospd01010_3", "gospd01010_4"}
        & set(goals)
    )


def primary_gospd_format(job: dict[str, Any]) -> Optional[str]:
    """兼容旧调用：返回勾选顺序中的第一份官方格式（无偏重）。"""
    from src.workflow.recipes import WORKPAPER_RECIPES

    goals = list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or [])
    seen: set[str] = set()
    for gid in goals:
        fmt = str((WORKPAPER_RECIPES.get(gid) or {}).get("workbook_format") or "").strip()
        if fmt and fmt not in seen:
            return fmt
    return None



def list_business_chains(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """列出当前单据中的业务链摘要。"""
    rows: list[dict[str, Any]] = []
    for chain_id, docs in group_classified_by_chain(list(classified or [])):
        types = sorted({str(d.get("doc_type") or "") for d in docs if d.get("doc_type")})
        rows.append(
            {
                "chain_id": chain_id,
                "doc_count": len(docs),
                "doc_types": types,
                "file_names": [str(d.get("file_name") or "") for d in docs],
            }
        )
    return rows


def docs_for_chain(
    classified: list[dict[str, Any]], chain_id: str
) -> list[dict[str, Any]]:
    cid = str(chain_id or "").strip()
    if not cid:
        return list(classified or [])
    for c_id, docs in group_classified_by_chain(list(classified or [])):
        if c_id == cid:
            return list(docs)
    return []


def sample_map(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("gospd_sample_results")
    return dict(raw) if isinstance(raw, dict) else {}


def get_sample(job: dict[str, Any], chain_id: str) -> dict[str, Any]:
    return dict(sample_map(job).get(str(chain_id) or "") or {})


def sample_matching_ok(job: dict[str, Any], chain_id: Optional[str] = None) -> bool:
    """当前笔 Gate4 是否已确认（兼容只写了顶层、未写入 sample 的旧状态）。"""
    cid = str(chain_id or job.get("active_chain_id") or "").strip()
    sample = get_sample(job, cid) if cid else {}
    if sample.get("matching_confirmed"):
        return True
    if not job.get("matching_confirmed"):
        return False
    # 顶层已确认：仅当无分笔或就是当前 active 笔时视为本笔已确认
    if not cid or str(job.get("active_chain_id") or "").strip() == cid:
        return True
    return False


def heal_sample_matching_from_job(job: dict[str, Any], chain_id: str) -> dict[str, Any]:
    """若顶层 Gate4 已确认而 sample 缺省，补写到当前笔（不改签名语义）。"""
    cid = str(chain_id or "").strip()
    if not cid or not job.get("matching_confirmed"):
        return job
    sample = get_sample(job, cid)
    if sample.get("matching_confirmed"):
        return job
    samples = merge_sample(
        job.get("gospd_sample_results") or {},
        chain_id=cid,
        patch={
            "matching_confirmed": True,
            "matching_confirm_sig": job.get("matching_confirm_sig"),
            "evidence": sample.get("evidence") or job.get("evidence"),
            "relations": list(sample.get("relations") or job.get("relations") or []),
            "duplicates": dict(sample.get("duplicates") or job.get("duplicates") or {}),
        },
    )
    out = dict(job)
    out["gospd_sample_results"] = samples
    return out


def sample_test_complete(sample: dict[str, Any], job: Optional[dict[str, Any]] = None) -> bool:
    """按当前计划必做测试判断该笔是否测完（01030 仅需三单；01010 需三项）。"""
    required = set((job or {}).get("plan", {}).get("required_steps") or [])
    if not required:
        # 兼容旧调用：默认三项
        return bool(
            sample.get("contract_terms")
            and sample.get("amount_test")
            and sample.get("three_way")
        )
    ok = True
    if STEP_CONTRACT in required:
        ok = ok and bool(sample.get("contract_terms"))
    if STEP_AMOUNT in required:
        ok = ok and bool(sample.get("amount_test"))
    if STEP_THREE_WAY in required:
        ok = ok and bool(sample.get("three_way"))
    return ok


def resolve_active_chain_id(
    job: dict[str, Any], *, preferred: Optional[str] = None
) -> Optional[str]:
    """解析当前业务笔。

    优先级：显式 preferred → 已选 active_chain_id → 未完成笔（新上传）→ 末笔。
    已选笔必须优先，否则「切换业务」后确认字段会写到另一笔，造成死循环。
    """
    from src.audit.sample_population import desk_sample_ids

    chains = list_business_chains(list(job.get("classified") or []))
    ids = [c["chain_id"] for c in chains]
    desk_ids = desk_sample_ids(job)
    if preferred and preferred in desk_ids:
        return preferred
    if not ids:
        return desk_ids[0] if desk_ids else None
    if preferred and preferred in ids:
        return preferred
    cur = str(job.get("active_chain_id") or "").strip()
    if cur in ids:
        return cur
    samples = sample_map(job)
    # 尚未选定：优先未测完的强业务号链（新上传通常在后）；「未识别」垫后
    strong_ids = [c for c in ids if c != "未识别业务号"]
    weak_ids = [c for c in ids if c == "未识别业务号"]
    for cid in list(reversed(strong_ids)) + weak_ids:
        if not sample_test_complete(
            get_sample({"gospd_sample_results": samples}, cid), job
        ):
            return cid
    return ids[-1]


def prune_samples_to_chains(
    samples: dict[str, Any], chain_ids: list[str]
) -> dict[str, Any]:
    keep = set(chain_ids)
    return {k: deepcopy(v) for k, v in (samples or {}).items() if k in keep}


def merge_sample(
    existing: dict[str, Any],
    *,
    chain_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    out = deepcopy(existing) if isinstance(existing, dict) else {}
    cur = dict(out.get(chain_id) or {})
    cur.update(patch)
    cur["chain_id"] = chain_id
    cur["updated_at"] = _utc_now()
    out[chain_id] = cur
    return out


def mirror_sample_to_job_fields(sample: dict[str, Any]) -> dict[str, Any]:
    """把当前笔结果镜像到 job 顶层，供现有 UI / 门禁读取。"""
    # 有测过/确认过的样本：字段视为该笔已核对（兼容旧样本无 fields_confirmed）
    fields_flag = sample.get("fields_confirmed")
    if fields_flag is None:
        fields_flag = bool(
            sample.get("matching_confirmed")
            or sample.get("evidence")
            or sample.get("three_way")
            or sample.get("amount_test")
            or sample.get("contract_terms")
            or sample.get("conclusion_confirmed")
        )
    return {
        "evidence": sample.get("evidence"),
        "amount_test": sample.get("amount_test"),
        "contract_terms": sample.get("contract_terms"),
        "three_way": sample.get("three_way"),
        "three_way_match": sample.get("three_way_match"),
        "cutoff_test": sample.get("cutoff_test"),
        "matching_confirmed": bool(sample.get("matching_confirmed")),
        "matching_confirm_sig": sample.get("matching_confirm_sig"),
        "relations": list(sample.get("relations") or []),
        "duplicates": dict(sample.get("duplicates") or {}),
        "fields_confirmed": bool(fields_flag),
        "fields_confirm_sig": sample.get("fields_confirm_sig"),
        # 注意：不镜像 conclusion_confirmed —— 顶层该字段表示「全链可导出」
        # 单笔结论看 gospd_sample_results[cid].conclusion_confirmed
    }


def all_chains_conclusion_confirmed(job: dict[str, Any]) -> bool:
    """导出前：所有强业务链均已对本笔确认结论。"""
    for c in list_business_chains(list(job.get("classified") or [])):
        cid = c["chain_id"]
        if cid == "未识别业务号":
            continue
        if not get_sample(job, cid).get("conclusion_confirmed"):
            return False
    return True


def chains_missing_conclusion(job: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for c in list_business_chains(list(job.get("classified") or [])):
        cid = c["chain_id"]
        if cid == "未识别业务号":
            continue
        if not get_sample(job, cid).get("conclusion_confirmed"):
            missing.append(cid)
    return missing


def chains_missing_tests(job: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for c in list_business_chains(list(job.get("classified") or [])):
        cid = c["chain_id"]
        if cid == "未识别业务号":
            continue
        if not sample_test_complete(get_sample(job, cid), job):
            missing.append(cid)
    return missing


def chain_ids_touching_files(
    classified: list[dict[str, Any]], file_names: set[str]
) -> set[str]:
    touched: set[str] = set()
    want = {str(x) for x in file_names if x}
    if not want:
        return touched
    for cid, docs in group_classified_by_chain(list(classified or [])):
        names = {str(d.get("file_name") or "") for d in docs}
        if names & want:
            touched.add(cid)
    return touched

"""演示 OCR 快路径：六笔样例用已落盘识别结果，跳过远程 OCR/抽字段。

按上传文件名命中；不是 Mock 虚构字段，而是上次真识别结果的回放。
环境变量 DEMO_OCR_CACHE=0 关闭。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from config.settings import settings

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "demo_ocr_cache"
INDEX_NAME = "index.json"
# 演示识别进度条墙钟（秒）：按批内并发均摊到每个文件
DEMO_OCR_WALL_SEC = 5.0


def demo_file_delay_sec(file_count: int, *, workers: int = 6) -> float:
    n = max(int(file_count or 0), 1)
    w = max(1, min(int(workers or 1), n))
    return DEMO_OCR_WALL_SEC * w / n

DEMO_NAME_RE = re.compile(
    r"(SO25-0281|HT25-0281|SO25-0282|KJHT25-0282|SO25-0285|EXHT25-0285|"
    r"SO25-0286|EXKJHT25-0286|SO25-0296|HT25-0296|"
    r"SO25-0021|HT25-0021|FH25-0021|QS25-0021|FP25-0021)",
    re.I,
)


def demo_cache_enabled() -> bool:
    raw = str(getattr(settings, "DEMO_OCR_CACHE", "1") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def is_demo_filename(filename: str) -> bool:
    return bool(DEMO_NAME_RE.search(Path(str(filename or "")).name))


def _safe_stem(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", name)[:180]


def cache_path_for(filename: str) -> Path:
    return CACHE_DIR / f"{_safe_stem(filename)}.json"


def _index() -> dict[str, str]:
    path = CACHE_DIR / INDEX_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = data.get("by_filename") if isinstance(data, dict) else None
    if isinstance(files, dict):
        return {str(k): str(v) for k, v in files.items()}
    return {}


def _resolve_cache_file(name: str) -> Path | None:
    idx = _index()
    rel = idx.get(name)
    if not rel:
        folded = {str(k).casefold(): str(v) for k, v in idx.items()}
        rel = folded.get(name.casefold())
    if not rel:
        hits = [v for k, v in idx.items() if k in name or name in k]
        if len(hits) == 1:
            rel = hits[0]
    path = CACHE_DIR / rel if rel else cache_path_for(name)
    return path if path.is_file() else None


def lookup_demo_ocr(filename: str) -> dict[str, Any] | None:
    if not demo_cache_enabled():
        return None
    name = Path(str(filename or "")).name
    if not name or not is_demo_filename(name):
        return None
    path = _resolve_cache_file(name)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _harvest_amount_ai_seeds(item: dict[str, Any]) -> list[dict[str, Any]]:
    """从已落库的金额卡抽出 AI 建议，演示时回放，不再调视觉。"""
    seeds: list[dict[str, Any]] = []
    for row in item.get("_amount_ambiguities") or []:
        if not isinstance(row, dict):
            continue
        rec = row.get("ai_recommendation")
        if not isinstance(rec, dict):
            continue
        fk = str(row.get("field_key") or "").strip()
        if not fk:
            continue
        preferred = rec.get("recommended_value")
        cid = str(rec.get("candidate_id") or "").strip()
        if preferred is None and cid:
            for c in row.get("candidates") or []:
                if isinstance(c, dict) and str(c.get("candidate_id") or "") == cid:
                    preferred = c.get("value")
                    break
        seed_rec = dict(rec)
        if preferred is not None:
            try:
                preferred_f = float(preferred)
            except (TypeError, ValueError):
                preferred_f = None
            if preferred_f is not None:
                seed_rec["recommended_value"] = preferred_f
                # 演示文案：旧视觉说明曾把候选说乱，按目标值写清口径
                if abs(preferred_f - 64660.8) <= 0.05 and fk == "amount":
                    seed_rec["reason"] = (
                        "票面同时出现折扣前商品金额 68,400.00 与折后未税。"
                        "金额（不含税）应按价税合计−税额取 64,660.80，不宜取折扣前 68,400。"
                    )
                    seed_rec["review_status"] = "RECOMMENDED"
                    seed_rec.setdefault("confidence", 0.92)
                    seed_rec.setdefault("provider", "demo_ocr_replay")
                    seed_rec.setdefault("model", "demo-cache")
        seeds.append(
            {
                "field_key": fk,
                "preferred_value": preferred,
                "ai_recommendation": seed_rec,
            }
        )
    return seeds


def apply_amount_ai_seeds(item: dict[str, Any], seeds: list[dict[str, Any]] | None) -> None:
    if not seeds or not isinstance(item, dict):
        return
    by_fk = {
        str(s.get("field_key") or ""): s
        for s in seeds
        if isinstance(s, dict) and s.get("field_key")
    }
    for row in item.get("_amount_ambiguities") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").upper() not in {
            "NEEDS_REVIEW",
            "INSUFFICIENT_EVIDENCE",
        }:
            continue
        if row.get("ai_recommendation"):
            continue
        seed = by_fk.get(str(row.get("field_key") or ""))
        if not seed:
            continue
        rec = seed.get("ai_recommendation") if isinstance(seed.get("ai_recommendation"), dict) else {}
        pref = seed.get("preferred_value")
        if pref is None:
            pref = rec.get("recommended_value")
        attached = None
        if pref is not None:
            try:
                want = float(pref)
            except (TypeError, ValueError):
                want = None
            if want is not None:
                for c in row.get("candidates") or []:
                    if not isinstance(c, dict):
                        continue
                    try:
                        val = float(c.get("value"))
                    except (TypeError, ValueError):
                        continue
                    if abs(val - want) <= 0.05:
                        attached = dict(rec)
                        attached["candidate_id"] = str(c.get("candidate_id") or "")
                        attached["recommended_value"] = want
                        break
        if attached is None and isinstance(rec, dict) and rec.get("candidate_id"):
            cid = str(rec.get("candidate_id") or "")
            if any(str(c.get("candidate_id") or "") == cid for c in (row.get("candidates") or []) if isinstance(c, dict)):
                attached = dict(rec)
        if attached:
            row["ai_recommendation"] = attached
            row["vision_attempted"] = True


def payload_from_classified_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = Path(str(item.get("file_name") or "")).name
    if not name or not is_demo_filename(name):
        return None
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    fields = dict(fields)
    meta = item.get("_field_meta") if isinstance(item.get("_field_meta"), dict) else {}
    for key, slot in meta.items():
        if not isinstance(slot, dict):
            continue
        raw_v = slot.get("raw_value")
        if raw_v is None:
            continue
        # 回放首次识别，不用人工确认后的值（否则 0296 折后数会把多金额卡吞掉）
        fields[key] = raw_v
    raw = str(item.get("raw_text") or "")
    if not fields and not raw.strip():
        return None
    keep = {
        "file_name": name,
        "doc_type": item.get("doc_type") or "other",
        "fields": fields,
        "raw_text": raw,
        "ocr_source": "demo_cache",
        "confidence": item.get("confidence"),
        "text_blocks": item.get("text_blocks") or [],
        "extract_field_keys": list(item.get("extract_field_keys") or []),
        "amount_ai_seeds": _harvest_amount_ai_seeds(item),
    }
    return keep


def harvest_from_job(job: dict[str, Any]) -> dict[str, Any]:
    """从 job.classified 写入缓存。同名保留字段更全的一份。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    best: dict[str, dict[str, Any]] = {}
    for item in job.get("classified") or []:
        payload = payload_from_classified_item(item if isinstance(item, dict) else {})
        if not payload:
            continue
        name = str(payload["file_name"])
        prev = best.get(name)
        score = len(str(payload.get("raw_text") or "")) + 10 * len(payload.get("fields") or {})
        if prev is None or score > int(prev.get("_score") or 0):
            payload["_score"] = score
            best[name] = payload
    by_filename: dict[str, str] = {}
    for name, payload in best.items():
        payload.pop("_score", None)
        rel = f"{_safe_stem(name)}.json"
        (CACHE_DIR / rel).write_text(
            json.dumps(payload, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
        by_filename[name] = rel
    index = {
        "updated": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "count": len(by_filename),
        "by_filename": by_filename,
    }
    (CACHE_DIR / INDEX_NAME).write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return index


def apply_demo_hit(
    *,
    filename: str,
    path: str,
    fingerprint: str,
    slot_hint: str,
    payload: dict[str, Any],
    source_packet: dict[str, Any] | None = None,
    packet_keys: dict[str, Any] | None = None,
    delay_sec: float = 0.18,
) -> dict[str, Any]:
    """把缓存结果套到本次上传文件上（新 path/指纹），装样子稍等一下。"""
    if delay_sec > 0:
        time.sleep(delay_sec)
    from src.models.field_values import seed_field_meta
    from src.workflow.pipeline import fallback_fields_from_filename, merge_fields

    fields = dict(payload.get("fields") or {})
    doc_type = str(payload.get("doc_type") or "other")
    fallback = fallback_fields_from_filename(filename, doc_type)
    if packet_keys:
        fallback = merge_fields(dict(packet_keys), fallback)
    fields = merge_fields(fields, fallback)
    item = {
        "file_name": Path(filename).name,
        "path": path,
        "doc_type": doc_type,
        "upload_slot": slot_hint,
        "fields": fields,
        "raw_text": str(payload.get("raw_text") or ""),
        "ocr_source": "demo_cache",
        "confidence": payload.get("confidence") if payload.get("confidence") is not None else 0.99,
        "text_blocks": list(payload.get("text_blocks") or []),
        "error": None,
        "file_fingerprint": fingerprint,
        "extract_field_keys": list(payload.get("extract_field_keys") or []),
        "demo_ocr_cache": True,
    }
    if source_packet:
        item["source_packet"] = dict(source_packet)
    seed_field_meta(item, source="demo_cache", extractor="demo_ocr_replay")
    from src.workflow.field_resolution.evidence_inventory import attach_document_evidence

    attach_document_evidence(item)
    try:
        from src.workflow.amount_ambiguity import scan_document

        scan_document(item)
        apply_amount_ai_seeds(item, payload.get("amount_ai_seeds") if isinstance(payload.get("amount_ai_seeds"), list) else None)
    except Exception:
        pass
    return item

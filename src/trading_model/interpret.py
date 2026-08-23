from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from uuid import uuid4

from .auditor_pack import build_auditor_pack
from .classify import classify_trade_mode, facts_from_text
from .constants import GATE_VERSION, PROMPT_VERSION, SCHEMA_VERSION
from .control import assess_control
from .dates import build_date_inventory
from .harvest import harvest
from .llm_judge import maybe_judge
from .llm_json import scrub_why_this_event, validate_excerpts
from .persist import ArtifactStore, FileArg, compute_input_fingerprint, file_content_hashes
from .rag.store import default_db_path, retrieve
from .slot import slot_fields
from .gospd01030 import project_gospd01030
from .workbook import contract_label_text, deterministic_actual, project_workbook


def _normalize_docs(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, item in enumerate(classified):
        out.append(
            {
                "document_id": item.get("document_id") or f"DOC-{i+1}",
                "document_type": item.get("doc_type") or item.get("document_type") or "other",
                "file_name": item.get("file_name") or "",
                "raw_text": item.get("raw_text") or "",
                "text_blocks": item.get("text_blocks") or [],
                "fields": item.get("fields") or {},
                "confidence": item.get("confidence"),
            }
        )
    return out


def interpret_trading_model(
    classified: Optional[list[dict[str, Any]]] = None,
    files: Optional[Sequence[FileArg]] = None,
    *,
    transaction_id: Optional[str] = None,
    use_llm: bool = True,
    persist: bool = True,
    ingest: Optional[bool] = None,
    data_root: Optional[Any] = None,
    ocr_fn: Optional[Callable[[Sequence[FileArg]], list[dict[str, Any]]]] = None,
    llm_fn: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    if ingest is True and use_llm:
        raise ValueError("ingest and use_llm cannot run in one call")
    do_ingest = bool(ingest) if ingest is not None else (not use_llm)
    tx = transaction_id or f"tx-{uuid4().hex[:12]}"
    root = data_root
    if root is None:
        root = Path(__file__).resolve().parent / "data"
    store = ArtifactStore(root)
    file_hashes = file_content_hashes(files)
    fingerprint = compute_input_fingerprint(classified, files)

    cached = store.lookup(fingerprint, file_hashes) if persist else None
    if cached:
        return project_workbook(cached["classification"]), cached

    ocr_invoked = False
    docs_in = list(classified or [])
    if use_llm and not docs_in and files:
        raise ValueError("judge path cannot invoke OCR; pass classified[]")
    if not docs_in and files:
        if ocr_fn is None:
            raise ValueError("files provided but ocr_fn is missing and no classified[]")
        docs_in = list(ocr_fn(files) or [])
        ocr_invoked = True

    docs = _normalize_docs(docs_in)
    rag_path = default_db_path(root)
    harvested = harvest(docs_in, rag_db=rag_path)
    rag_query = "控制权 交货 已装船 签收 验收 收入确认"
    if harvested.get("nominal_code"):
        rag_query = f"{harvested['nominal_code']} {rag_query}"
    rag_hits = retrieve(rag_query, db_path=rag_path, k=8)
    embedder_id = "char_ngram_v1"
    contract_hits: list[dict[str, Any]] = []
    store_chunks: list[dict[str, Any]] = []
    cr_root = Path(root) / "contract_rag"
    from .contract_rag.chroma_store import EmbedDimError

    try:
        from .contract_rag.embedder import embedder_name
        from .contract_rag.ingest import ingest_text
        from .contract_rag.search import hybrid_search
        from .contract_rag.sqlite_store import connect, fetch_all_paragraphs

        embedder_id = embedder_name()
        if do_ingest:
            for item in docs_in:
                text = str(item.get("raw_text") or "").strip()
                if not text:
                    continue
                name = f"{item.get('document_id') or 'doc'}.md"
                ingest_text(text, source_name=name, data_root=cr_root)
        if any(str(item.get("raw_text") or "").strip() for item in docs_in):
            contract_hits = hybrid_search(rag_query, data_root=cr_root, top_n=8)
        conn = connect(cr_root)
        store_chunks = fetch_all_paragraphs(conn)
        conn.close()
    except EmbedDimError:
        raise
    except Exception:
        contract_hits = []
        store_chunks = []
    if not store_chunks:
        from .sim.paragraphs import paragraphs_from_classified

        store_chunks = paragraphs_from_classified(docs_in)
    slots = slot_fields(docs_in, harvested)
    dates = build_date_inventory(docs_in)
    facts = facts_from_text(docs_in)
    control, missing, questions = assess_control(docs_in, dates)
    classified_out = classify_trade_mode(harvested, slots, facts, docs_in)
    if use_llm is False and classified_out.get("confidence") == "high":
        classified_out["confidence"] = "medium"

    llm_raw, llm_invoked, skip_reason = maybe_judge(
        {
            "harvest": harvested,
            "slots": slots,
            "date_inventory": dates,
            "control": control,
            "rag_hits": rag_hits,
            "contract_hits": [
                {"id": h.get("id"), "seq": h.get("seq"), "raw_text": h.get("raw_text"), "rrf_score": h.get("rrf_score")}
                for h in contract_hits
            ],
            "judgment_chunks": [
                {"id": c.get("id"), "source_file": c.get("source_file"), "seq": c.get("seq"), "raw_text": c.get("raw_text")}
                for c in store_chunks
            ],
            "documents": [
                {
                    "document_id": d.get("document_id"),
                    "file_name": d.get("file_name"),
                    "doc_type": d.get("doc_type") or d.get("document_type"),
                    "raw_text": d.get("raw_text"),
                }
                for d in docs_in
            ],
            "embedder": embedder_id,
        },
        use_llm=use_llm,
        llm_fn=llm_fn,
    )
    excerpt_validation: list[dict[str, Any]] = []
    model_id = None
    if llm_invoked and isinstance(llm_raw, dict):
        llm_raw = scrub_why_this_event(llm_raw)
        model_id = llm_raw.pop("_model_id", None)
        extra_q = llm_raw.get("manual_review_questions") or []
        if isinstance(extra_q, list):
            questions.extend(str(x) for x in extra_q)
        source_text = "\n".join(str(d.get("raw_text") or "") for d in docs_in)
        excerpt_validation = validate_excerpts(llm_raw, source_text)
        llm_raw.pop("why_this_event", None)

    can_conclude, actual_scenario = deterministic_actual(classified_out)
    advisory: Optional[dict[str, Any]] = None
    if llm_invoked and isinstance(llm_raw, dict):
        excerpt_ok = all(bool(item.get("ok", True)) for item in excerpt_validation)
        llm_can = llm_raw.get("can_conclude")
        llm_actual = str(
            llm_raw.get("actual_scenario")
            or (llm_raw.get("classification") or {}).get("conclusion")
            or ""
        ).strip()
        advisory = {
            "can_conclude": llm_can,
            "actual_scenario": llm_actual,
            "excerpt_ok": excerpt_ok,
            "wrote_workbook_text": False,
        }
        if llm_can is False or (excerpt_validation and not excerpt_ok):
            can_conclude = False
            actual_scenario = ""
            classified_out["status"] = "insufficient_evidence"
            classified_out["confidence"] = "no_conclusion"

    pack = build_auditor_pack(docs_in, harvested, classified_out, control, missing)
    classification = {
        "nominal_incoterm": classified_out["nominal_incoterm"],
        "actual_fulfillment_profile": classified_out["actual_fulfillment_profile"],
        "status": classified_out["status"],
        "candidate_profile": classified_out["candidate_profile"],
        "confidence": classified_out["confidence"],
        "actual_scenario": actual_scenario if can_conclude else "",
        "can_conclude": can_conclude,
        "contract_label": contract_label_text(classified_out),
        "conclusion": "",
    }
    view = project_workbook(classification)
    classification["conclusion"] = view["trading_mode_conclusion"]

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_meta": {
            "transaction_id": tx,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": fingerprint,
            "ocr_invoked": ocr_invoked,
            "ingest_invoked": do_ingest,
            "llm_invoked": llm_invoked,
            "llm_skipped_reason": skip_reason,
            "prompt_version": PROMPT_VERSION,
            "dict_version": "1.2",
            "gate_version": GATE_VERSION,
            "model_id": model_id,
            "embedder": embedder_id,
            "source_file_hashes": file_hashes,
            "ocr_text_fingerprint": None,
        },
        "documents": docs,
        "harvest": {"spans": harvested.get("spans") or []},
        "rag": {
            "query": rag_query,
            "hits": rag_hits,
            "contract_hits": contract_hits,
            "review_chunks": store_chunks if not can_conclude else [],
            "embedder": embedder_id,
        },
        "slots": slots,
        "date_inventory": dates,
        "control_transfer_assessment": control,
        "classification": classification,
        "actual_fulfillment_profile": classified_out["actual_fulfillment_profile"],
        "gates": [],
        "conflicts": [],
        "missing_documents": missing,
        "manual_review_questions": questions,
        "llm": {
            "raw_response": llm_raw,
            "excerpt_validation": excerpt_validation,
            "advisory": advisory,
        },
        "auditor_review_pack": pack,
        "gospd01030": project_gospd01030(
            classification=classification,
            control=control,
            documents=docs,
            missing_documents=missing,
        ),
    }
    if persist:
        store.save(tx, artifact)
    return view, artifact

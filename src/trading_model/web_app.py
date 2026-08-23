from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATIC = Path(__file__).resolve().parent / "web"
SCAN_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
SCAN_MAX_BYTES = 20 * 1024 * 1024


class InterpretIn(BaseModel):
    raw_text: str = Field(..., min_length=1)
    document_id: str = "CON-1"
    doc_type: str = "sales_contract"
    use_llm: bool = False
    persist: bool = False


class SimRunIn(BaseModel):
    scene_id: str = "fob_standard"
    raw_text: str = ""
    use_llm: bool = False


class AuditorAskIn(BaseModel):
    question: str = Field(..., min_length=1)
    paragraphs: list[dict[str, Any]]
    hits: list[dict[str, Any]] = Field(default_factory=list)


class IngestIn(BaseModel):
    raw_text: str = Field(..., min_length=1)
    document_id: str = "CON-1"


class JudgeIn(BaseModel):
    raw_text: str = Field(..., min_length=1)
    document_id: str = "CON-1"
    doc_type: str = "sales_contract"
    use_llm: bool = False


def _trim_reg_hit(hit: dict[str, Any]) -> dict[str, Any]:
    excerpt = str(hit.get("excerpt") or hit.get("body") or "")
    return {
        "source": hit.get("source"),
        "title": hit.get("title"),
        "excerpt": excerpt[:420],
        "rank": hit.get("rank"),
        "backend": hit.get("backend"),
    }


def _trim_contract_hit(hit: dict[str, Any]) -> dict[str, Any]:
    text = str(hit.get("raw_text") or "")
    return {
        "id": hit.get("id"),
        "seq": hit.get("seq"),
        "raw_text": text[:420],
        "rrf_score": hit.get("rrf_score"),
    }


def _trim_review_chunk(hit: dict[str, Any]) -> dict[str, Any]:
    text = str(hit.get("raw_text") or "")
    return {
        "id": hit.get("id"),
        "source_file": hit.get("source_file"),
        "seq": hit.get("seq"),
        "raw_text": text[:800],
    }


def _rag_payload(rag: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": rag.get("query"),
        "embedder": rag.get("embedder"),
        "hits": [_trim_reg_hit(h) for h in (rag.get("hits") or [])[:8]],
        "contract_hits": [_trim_contract_hit(h) for h in (rag.get("contract_hits") or [])[:8]],
        "review_chunks": [_trim_review_chunk(h) for h in (rag.get("review_chunks") or [])],
    }


def _classified_from_text(document_id: str, doc_type: str, raw_text: str) -> list[dict[str, Any]]:
    return [
        {
            "document_id": document_id,
            "doc_type": doc_type,
            "file_name": f"{document_id}.txt",
            "raw_text": raw_text,
            "text_blocks": [],
            "fields": {},
            "confidence": 0.99,
        }
    ]


def _interpret_payload(view: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    rag = artifact.get("rag") or {}
    cls = artifact.get("classification") or {}
    return {
        "view": view,
        "run_meta": artifact.get("run_meta") or {},
        "classification": cls,
        "harvest_spans": (artifact.get("harvest") or {}).get("spans") or [],
        "control": artifact.get("control_transfer_assessment") or {},
        "can_conclude": cls.get("can_conclude"),
        "contract_label": cls.get("contract_label") or "",
        "llm_advisory": (artifact.get("llm") or {}).get("advisory"),
        "rag": _rag_payload(rag),
        "gospd01030": artifact.get("gospd01030") or {},
    }


def create_app(data_root: Optional[Path] = None) -> FastAPI:
    root = Path(data_root) if data_root else Path(__file__).resolve().parent / "data" / "web"
    app = FastAPI(title="Trading model lab")
    app.state.data_root = root
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        page = STATIC / "index.html"
        if not page.exists():
            raise HTTPException(500, "index.html missing")
        return FileResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/v1/status")
    def status() -> dict[str, Any]:
        import os

        from src.trading_model.contract_rag.embedder import dimension_for, resolve_embed_model, resolve_profile

        choice = (os.environ.get("CONTRACT_RAG_EMBEDDER") or "").strip().lower()
        if choice in {"hash", "test", "ngram"}:
            embedder = "hash"
        else:
            embedder = resolve_embed_model()
        return {
            "embedder": embedder,
            "dim": dimension_for(embedder),
            "profile": resolve_profile(),
            "ok": True,
        }

    @app.post("/v1/interpret")
    def interpret(payload: InterpretIn) -> dict[str, Any]:
        from src.trading_model.interpret import interpret_trading_model

        view, artifact = interpret_trading_model(
            classified=_classified_from_text(payload.document_id, payload.doc_type, payload.raw_text),
            use_llm=payload.use_llm,
            persist=payload.persist,
            data_root=app.state.data_root,
            transaction_id=f"web-{uuid4().hex[:10]}",
        )
        return _interpret_payload(view, artifact)

    @app.post("/v1/ingest")
    def ingest(payload: IngestIn) -> dict[str, Any]:
        from src.trading_model.contract_rag.ingest import ingest_text

        cr_root = Path(app.state.data_root) / "contract_rag"
        rows = ingest_text(
            payload.raw_text,
            source_name=f"{payload.document_id}.md",
            data_root=cr_root,
        )
        return {
            "ingested": len(rows),
            "llm_invoked": False,
            "source_file": f"{payload.document_id}.md",
        }

    @app.post("/v1/ingest/file")
    def ingest_file(
        file: UploadFile = File(...),
        document_id: str = Form("SCAN-1"),
    ) -> dict[str, Any]:
        from src.trading_model.contract_rag.ingest import extract_document, ingest_text

        filename = file.filename or "scan.pdf"
        suffix = Path(filename).suffix.lower()
        if suffix not in SCAN_SUFFIXES:
            raise HTTPException(400, "unsupported file type")
        raw = file.file.read()
        if not raw:
            raise HTTPException(400, "empty file")
        if len(raw) > SCAN_MAX_BYTES:
            raise HTTPException(400, "file too large")
        stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in (document_id or "SCAN-1"))
        inbox = Path(app.state.data_root) / "contract_rag" / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        saved = inbox / f"{stem}{suffix}"
        saved.write_bytes(raw)
        name, markdown, method = extract_document(saved)
        rows = ingest_text(
            markdown,
            source_name=f"{Path(name).stem}.md",
            data_root=Path(app.state.data_root) / "contract_rag",
        )
        preview = "\n\n".join(str(r.get("raw_text") or "") for r in rows)
        return {
            "ingested": len(rows),
            "llm_invoked": False,
            "ocr_invoked": method == "ocr",
            "extract_method": method,
            "source_file": name,
            "preview": preview[:8000],
        }

    @app.post("/v1/judge")
    def judge(payload: JudgeIn) -> dict[str, Any]:
        from src.trading_model.interpret import interpret_trading_model

        try:
            view, artifact = interpret_trading_model(
                classified=_classified_from_text(payload.document_id, payload.doc_type, payload.raw_text),
                use_llm=payload.use_llm,
                ingest=False,
                persist=False,
                data_root=app.state.data_root,
                transaction_id=f"judge-{uuid4().hex[:10]}",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _interpret_payload(view, artifact)

    @app.get("/v1/sim/scenes")
    def sim_scenes() -> dict[str, Any]:
        from src.trading_model.sim.ocr import list_scenes

        return {"scenes": list_scenes()}

    @app.post("/v1/sim/run")
    def sim_run(payload: SimRunIn) -> dict[str, Any]:
        from src.trading_model.sim.run import run_sim

        try:
            out = run_sim(
                payload.scene_id.strip() or "fob_standard",
                data_root=app.state.data_root,
                raw_text=payload.raw_text or None,
                use_llm=payload.use_llm,
            )
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        out["rag"] = _rag_payload(out.get("rag") or {})
        return out

    @app.post("/v1/auditor/ask")
    def auditor_ask(payload: AuditorAskIn) -> dict[str, Any]:
        from src.trading_model.auditor_chat import ask_auditor

        try:
            return ask_auditor(payload.question, payload.paragraphs, hits=payload.hits)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8766, log_level="info")


if __name__ == "__main__":
    main()

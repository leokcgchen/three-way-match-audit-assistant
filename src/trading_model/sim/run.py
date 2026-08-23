from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from src.trading_model.interpret import interpret_trading_model
from src.trading_model.sim.ocr import list_scenes, mock_ocr
from src.trading_model.sim.paragraphs import paragraphs_from_classified

__all__ = ["list_scenes", "run_sim"]


def _step(step_id: str, label: str, status: str, detail: str = "") -> dict[str, str]:
    return {"id": step_id, "label": label, "status": status, "detail": detail}


def run_sim(
    scene_id: str,
    *,
    data_root: Path,
    raw_text: Optional[str] = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    steps = [
        _step("scene", "选样例", "running"),
        _step("ocr", "模拟 OCR", "pending"),
        _step("rag", "双轨检索", "pending"),
        _step("judge", "贸易模式", "pending"),
    ]
    try:
        classified = mock_ocr(scene_id, raw_text=raw_text)
    except Exception as exc:
        steps[0]["status"] = "error"
        steps[0]["detail"] = str(exc)
        return {"steps": steps, "view": {}, "rag": {}, "paragraphs": [], "classified": []}
    steps[0]["status"] = "done"
    steps[0]["detail"] = scene_id
    steps[1]["status"] = "done"
    steps[1]["detail"] = ",".join(str(d.get("file_name") or "") for d in classified)
    run_root = Path(data_root) / "sim_runs" / uuid4().hex[:12]
    run_root.mkdir(parents=True, exist_ok=True)
    view, artifact = interpret_trading_model(
        classified=classified,
        use_llm=False,
        persist=False,
        data_root=run_root,
        transaction_id=f"sim-{uuid4().hex[:10]}",
    )
    if use_llm:
        view, artifact = interpret_trading_model(
            classified=classified,
            use_llm=True,
            persist=False,
            data_root=run_root,
            transaction_id=f"sim-{uuid4().hex[:10]}",
        )
    rag = artifact.get("rag") or {}
    steps[2]["status"] = "done"
    steps[2]["detail"] = str(rag.get("embedder") or "")
    steps[3]["status"] = "done"
    steps[3]["detail"] = view.get("status") or ""
    return {
        "steps": steps,
        "view": view,
        "run_meta": artifact.get("run_meta") or {},
        "classification": artifact.get("classification") or {},
        "rag": {
            "query": rag.get("query"),
            "embedder": rag.get("embedder"),
            "hits": rag.get("hits") or [],
            "contract_hits": rag.get("contract_hits") or [],
            "review_chunks": rag.get("review_chunks") or [],
        },
        "can_conclude": (artifact.get("classification") or {}).get("can_conclude"),
        "contract_label": (artifact.get("classification") or {}).get("contract_label") or "",
        "llm_advisory": (artifact.get("llm") or {}).get("advisory"),
        "gospd01030": artifact.get("gospd01030") or {},
        "paragraphs": paragraphs_from_classified(classified),
        "classified": classified,
    }

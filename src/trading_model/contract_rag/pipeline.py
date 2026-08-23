from __future__ import annotations

from typing import Any, Callable, Optional

from .extract import extract_trade_mode
from .ingest import ingest_source
from .search import hybrid_search
from .sqlite_store import default_data_root, init_stores

DEFAULT_QUERY = "合同的贸易术语 / 运输条款是什么？"


def analyze_contract(
    source: Any,
    *,
    data_root: Optional[Any] = None,
    query: str = DEFAULT_QUERY,
    top_n: int = 8,
    chroma_n: int = 20,
    fts_n: int = 20,
    rrf_k: int = 60,
    ocr_fn: Optional[Callable[[Any], str]] = None,
    embedder: Any = None,
    llm_fn: Optional[Callable[[str], dict]] = None,
    replace_source: bool = True,
) -> dict[str, Any]:
    """Experimental: retrieve paragraphs and read the written Incoterms label.

    This is not actual fulfillment and is not used by interpret_trading_model.
    Product conclusions come from classify + workbook on the full file set.
    """
    root = default_data_root(data_root)
    init_stores(root)
    ingest_source(
        source,
        data_root=root,
        ocr_fn=ocr_fn,
        embedder=embedder,
        replace_source=replace_source,
    )
    hits = hybrid_search(
        query,
        data_root=root,
        top_n=top_n,
        chroma_n=chroma_n,
        fts_n=fts_n,
        rrf_k=rrf_k,
        embedder=embedder,
    )
    extraction = extract_trade_mode(hits, llm_fn=llm_fn)
    return {
        "experimental": True,
        "kind": "contract_label_only",
        "not_actual_fulfillment": True,
        "contract_label": extraction["trade_mode"],
        "confidence": extraction["confidence"],
        "evidence": extraction["evidence"],
        "model_id": extraction.get("model_id"),
        "skipped": extraction.get("skipped"),
        "hits": hits,
        "data_root": str(root),
    }

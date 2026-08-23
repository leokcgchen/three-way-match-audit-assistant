from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .constants import INCOTERM_CODES

_TERM_RE = re.compile(r"\b(" + "|".join(INCOTERM_CODES) + r")\b", re.I)
_VERSION_RE = re.compile(r"Incoterms\s*®?\s*(20\d{2})", re.I)
_CONFIG_DIR = Path(__file__).resolve().parent / "config"

_DOC_TYPE_ALIAS = {
    "contract": "sales_contract",
    "sales_contract": "sales_contract",
    "invoice": "commercial_invoice",
    "vat_invoice": "commercial_invoice",
    "bl": "bill_of_lading",
    "bol": "bill_of_lading",
    "receipt": "delivery_receipt",
    "pod": "delivery_receipt",
    "grn": "warehouse",
}


def _load_json(name: str) -> Any:
    return json.loads((_CONFIG_DIR / name).read_text(encoding="utf-8"))


_ATLAS_CACHE: dict[str, Any] | None = None
_LEXICON_CACHE: dict[str, list[str]] | None = None
_CATALOG_TOPIC = {
    "transportTerms": "nominal_term",
    "controlTransferTerms": "control",
    "acceptanceDate": "acceptance",
    "deliveryDate": "delivery",
    "paymentTerms": "other",
}
_RAG_SEED = "交货 交付 装船 控制权 风险转移 运费 订舱 签收 验收 已装船 收入确认"
_TOPIC_HINTS = (
    ("delivery", ("交货", "交付", "装船", "装运", "提单", "on board", "承运")),
    ("risk", ("风险", "灭失", "所有权")),
    ("control", ("控制权", "置于买方处置", "无异议")),
    ("freight", ("运费", "订舱", "freight")),
    ("insurance", ("保险", "投保", "insured")),
    ("acceptance", ("验收", "签收", "检验", "无异议期限")),
    ("cost", ("包装", "装箱", "thc")),
)


def _atlas() -> dict[str, list[dict[str, Any]]]:
    global _ATLAS_CACHE
    if _ATLAS_CACHE is None:
        _ATLAS_CACHE = _load_json("section_atlas.json")
    return _ATLAS_CACHE


def _lexicon() -> dict[str, list[str]]:
    global _LEXICON_CACHE
    if _LEXICON_CACHE is None:
        merged: dict[str, list[str]] = {k: list(v) for k, v in _load_json("concept_lexicon.json").items()}
        catalog_path = _CONFIG_DIR / "field_catalog_trade.json"
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            for key, phrases in catalog.items():
                topic = _CATALOG_TOPIC.get(key, "other")
                bucket = merged.setdefault(topic, [])
                for phrase in phrases:
                    if phrase not in bucket:
                        bucket.append(phrase)
        _LEXICON_CACHE = merged
    return _LEXICON_CACHE


def _guess_topic(phrase: str) -> str:
    low = phrase.lower()
    for topic, hints in _TOPIC_HINTS:
        if any(h.lower() in low for h in hints):
            return topic
    return "other"


def _norm_type(doc: dict[str, Any]) -> str:
    raw = str(doc.get("doc_type") or doc.get("document_type") or "other").strip().lower()
    return _DOC_TYPE_ALIAS.get(raw, raw)


def _all_text(classified: list[dict[str, Any]]) -> str:
    return "\n".join(str(d.get("raw_text") or "") for d in classified)


def _heading_regex(headings: list[str]) -> re.Pattern[str]:
    parts = [re.escape(h) for h in sorted(headings, key=len, reverse=True)]
    return re.compile(
        r"(?:^|[\n。；]|第\s*\d+\s*条\s*)[ \t]*(" + "|".join(parts) + r")\b",
        re.I | re.M,
    )


def _next_heading_start(text: str, after: int, heading_rx: re.Pattern[str]) -> int:
    m = heading_rx.search(text, after)
    return m.start() if m else len(text)


def _enclosing_block(text: str, start: int, end: int, *, max_len: int = 900) -> str:
    """Take the clause/paragraph around a hit, not a 20–80 character window."""
    left_bound = 0
    for sep in ("\n\n", "\n第", "。", "；"):
        pos = text.rfind(sep, 0, start)
        if pos >= 0:
            left_bound = max(left_bound, pos + (0 if sep == "\n\n" else len(sep)))
    right_bound = len(text)
    for sep in ("\n\n", "\n第", "。"):
        pos = text.find(sep, end)
        if pos >= 0:
            right_bound = min(right_bound, pos + (1 if sep == "。" else 0))
    block = text[left_bound:right_bound].strip()
    if len(block) > max_len:
        block = text[max(left_bound, start - 200) : min(right_bound, end + 400)].strip()
    return block


def _add_span(
    spans: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    doc_id: str,
    topic: str,
    excerpt: str,
    section: str,
    channel: str,
) -> None:
    excerpt = " ".join(excerpt.split())
    if len(excerpt) < 4:
        return
    key = (doc_id, topic, excerpt[:180])
    if key in seen:
        return
    seen.add(key)
    spans.append(
        {
            "span_id": f"s{len(spans) + 1}",
            "document_id": doc_id,
            "page_or_section": section,
            "verbatim_excerpt": excerpt,
            "topic": topic,
            "channel": channel,
        }
    )


def _harvest_sections(doc: dict[str, Any], text: str, spans: list[dict[str, Any]], seen: set) -> None:
    doc_id = str(doc.get("document_id") or doc.get("file_name") or "")
    atlas = _atlas().get(_norm_type(doc)) or _atlas().get("sales_contract") or []
    all_headings: list[str] = []
    heading_topic: list[tuple[str, str]] = []
    for row in atlas:
        topic = row["topic"]
        for h in row["headings"]:
            all_headings.append(h)
            heading_topic.append((h.lower(), topic))
    if not all_headings:
        return
    rx = _heading_regex(all_headings)
    for m in rx.finditer(text):
        title = m.group(1)
        topic = next((t for h, t in heading_topic if h == title.lower()), "other")
        body_start = m.end()
        body_end = _next_heading_start(text, body_start + 1, rx)
        excerpt = text[m.start() : body_end].strip()
        _add_span(
            spans,
            seen,
            doc_id=doc_id,
            topic=topic,
            excerpt=excerpt,
            section=title.strip(),
            channel="section_atlas",
        )


def _harvest_lexicon(doc: dict[str, Any], text: str, spans: list[dict[str, Any]], seen: set) -> None:
    doc_id = str(doc.get("document_id") or doc.get("file_name") or "")
    for topic, phrases in _lexicon().items():
        topic_key = "delivery" if topic == "nominal_term" else topic
        for phrase in phrases:
            pat = re.escape(phrase)
            if phrase.isascii() and len(phrase) <= 4:
                pat = r"\b" + pat + r"\b"
            for m in re.finditer(pat, text, re.I):
                excerpt = _enclosing_block(text, m.start(), m.end())
                _add_span(
                    spans,
                    seen,
                    doc_id=doc_id,
                    topic=topic_key,
                    excerpt=excerpt,
                    section="lexicon",
                    channel="concept_lexicon",
                )


def _harvest_rag_expand(
    doc: dict[str, Any],
    text: str,
    spans: list[dict[str, Any]],
    seen: set,
    rag_db: Path,
) -> None:
    from .rag.store import related_phrases

    doc_id = str(doc.get("document_id") or doc.get("file_name") or "")
    query = _RAG_SEED
    tm = _TERM_RE.search(text)
    if tm:
        query = f"{query} {tm.group(1)}"
    phrases = related_phrases(query, db_path=rag_db, k=8)
    for phrase in phrases:
        if len(phrase) < 4:
            continue
        pat = re.escape(phrase)
        if phrase.isascii() and len(phrase) <= 4:
            pat = r"\b" + pat + r"\b"
        try:
            matcher = re.compile(pat, re.I)
        except re.error:
            continue
        for m in matcher.finditer(text):
            excerpt = _enclosing_block(text, m.start(), m.end())
            _add_span(
                spans,
                seen,
                doc_id=doc_id,
                topic=_guess_topic(phrase),
                excerpt=excerpt,
                section="rag",
                channel="rag_expand",
            )


def harvest(
    classified: list[dict[str, Any]],
    *,
    rag_db: Optional[Path] = None,
) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for doc in classified:
        text = str(doc.get("raw_text") or "")
        if not text.strip():
            continue
        _harvest_sections(doc, text, spans, seen)
        _harvest_lexicon(doc, text, spans, seen)
        if rag_db is not None:
            _harvest_rag_expand(doc, text, spans, seen, Path(rag_db))

    blob = _all_text(classified)
    terms = [m.group(1).upper() for m in _TERM_RE.finditer(blob)]
    version = None
    vm = _VERSION_RE.search(blob)
    if vm:
        version = f"Incoterms {vm.group(1)}"
    place = None
    tm = _TERM_RE.search(blob)
    if tm:
        after = blob[tm.end() : tm.end() + 40]
        pm = re.match(r"[\s,，:：]+([A-Za-z][A-Za-z\s]{1,40}?)(?=\s+Incoterms|\s*$|[。．])", after)
        if pm:
            place = pm.group(1).strip()
        else:
            pm2 = re.match(r"[\s,，:：]+([A-Za-z]+)", after)
            if pm2 and pm2.group(1).lower() not in {"incoterms", "but", "the"}:
                place = pm2.group(1).strip()
    return {
        "spans": spans,
        "nominal_code": terms[0] if terms else None,
        "named_place_or_port": place,
        "version": version,
        "full_text": blob,
    }

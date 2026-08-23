"""Amount ambiguity scan + decide helpers (advisory until human confirms).

Persists on each classified document as ``item["_amount_ambiguities"]``.
Never writes ``accepted_value`` here — only ``decide_*`` paths may call ``accept_field``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.legacy_ocr.amount_resolve import AMOUNT_TOKEN_RE, TOTAL_AMOUNT_LABELS, _parse_number, in_html_detail_table_header, to_ascii_digits
from src.models.field_values import accept_field, effective_fields
from src.workflow.chain_workspace import docs_for_chain, is_gospd_mode, resolve_active_chain_id
from src.workflow.field_catalog import amount_field_spec, field_label

AMBIGUITY_KEY = "_amount_ambiguities"
OPEN_STATUSES = frozenset({"NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE"})
CLOSED_STATUSES = frozenset({"CONFIRMED", "REJECTED", "SYSTEM_CONSISTENT"})
TARGET_FIELDS = ("amount", "taxAmount", "totalAmount")

# label → role for mining
_LABEL_ROLE_PATTERNS: list[tuple[str, str]] = [
    ("价税合计", "tax_inclusive"),
    ("含税总金额", "tax_inclusive"),
    ("含税合计", "tax_inclusive"),
    ("本次应收", "tax_inclusive"),
    ("合计金额", "unknown_total"),
    ("总金额", "unknown_total"),
    ("总额", "unknown_total"),
    ("金额合计", "unknown_total"),
    ("合计（不含税）", "tax_exclusive"),
    ("合计不含税", "tax_exclusive"),
    ("折后不含税金额", "tax_exclusive"),
    ("折后不含税", "tax_exclusive"),
    ("不含税金额", "tax_exclusive"),
    ("未税金额", "tax_exclusive"),
    ("税额合计", "tax_only"),
    ("合计税额", "tax_only"),
    ("增值税额", "tax_only"),
    ("税额", "tax_only"),
    ("折扣前商品金额", "pre_discount"),
    ("折扣前", "pre_discount"),
    ("商业折扣", "discount"),
    ("授信上限", "credit_limit"),
    ("授信额度", "credit_limit"),
    ("内部授信", "credit_limit"),
    ("可用授信", "credit_available"),
    ("可用额度", "credit_available"),
    ("信用额度", "credit_limit"),
]

_ROLE_TO_FIELD = {
    "tax_exclusive": "amount",
    "tax_only": "taxAmount",
    "tax_inclusive": "totalAmount",
    "unknown_total": "totalAmount",
}

_INTERFERENCE_ROLES = frozenset(
    {"pre_discount", "discount", "credit_limit", "credit_available", "line_item"}
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round2(value: float) -> float:
    return round(float(value), 2)


def _is_amount_fragment(small: float, big: float) -> bool:
    """9.68 相对 9683.98：OCR 把千分位逗号当成小数点后的残段。"""
    if small <= 0 or big <= 0 or small >= big:
        return False
    if abs(small * 1000.0 - big) <= max(1.0, big * 0.02):
        return True
    sd = re.sub(r"\D", "", f"{small:.2f}").rstrip("0")
    bd = re.sub(r"\D", "", f"{big:.2f}").rstrip("0")
    return bool(sd and bd and sd != bd and (bd.startswith(sd) or sd in bd))


def _num(value: Any) -> float | None:
    return _parse_number(value)


def _looks_like_item_code(token: str) -> bool:
    """物料/行号：-01357、01357 这类前导零，不能当金额。"""
    t = re.sub(r"[¥￥$\s]", "", to_ascii_digits(str(token or "")))
    if re.search(r"-0\d{3,}", t):
        return True
    core = t.lstrip("-").split(".")[0].split(",")[0]
    return bool(core.startswith("0") and len(core) >= 4)


def _is_price_amount(val: float | None, token: str, role: str) -> bool:
    """价税字段只收正数；折扣才允许负数。"""
    if val is None:
        return False
    if role == "discount":
        return abs(float(val)) >= 0.01
    if float(val) <= 0:
        return False
    return not _looks_like_item_code(token)


def _is_percent_token(text: str, match_end: int) -> bool:
    return bool(re.match(r"\s*%", text[match_end : match_end + 4]))


def _looks_like_account_number(val: float | None, token: str) -> bool:
    if val is None:
        return False
    digits = re.sub(r"\D", "", token or "")
    return float(val) >= 1e10 or len(digits) >= 12


def _label_glued_to_unit_or_rate(text: str, match_end: int) -> bool:
    """「折后不含税单价」「商业折扣率」不是单据金额标签。"""
    rest = (text or "")[match_end : match_end + 4]
    return rest.startswith("单价") or rest.startswith("率")


def _plausible_competing_amount(
    val: float,
    *,
    current: float | None,
    quantity: float | None,
    amount: float | None,
    total: float | None,
) -> bool:
    """税率/数量/单价/账号不能跟单据金额抢「多金额」。"""
    if val <= 0:
        return False
    if val >= 1e10:
        return False
    if quantity is not None and abs(val - float(quantity)) < 0.009:
        return False
    base = current if current is not None else (amount if amount is not None else total)
    if quantity is not None and base is not None and float(quantity) >= 1:
        prod = float(quantity) * val
        if abs(prod - float(base)) <= max(0.05, abs(float(base)) * 0.02):
            return False
        if amount is not None and abs(prod - float(amount)) <= max(0.05, abs(float(amount)) * 0.02):
            return False
    if current is not None and amount is not None and abs(current - float(amount)) > 0.05:
        if abs(val - float(amount)) <= 0.05:
            return False
    if current is not None and total is not None and abs(current - float(total)) > 0.05:
        if abs(val - float(total)) <= 0.05:
            return False
    if base is not None and float(base) >= 80:
        if val < 50 and abs(val - round(val)) < 0.001:
            return False
        if val * 20 < float(base):
            return False
    return True


def _first_price_after(text: str, start: int, role: str, *, window: int = 160) -> tuple[float, str] | None:
    chunk = text[start : start + window]
    nl = chunk.find("\n")
    if nl >= 0:
        chunk = chunk[:nl]
    chunk = re.sub(r"^(?:\s*[（(]?\s*小写[）)]?\s*[¥￥$]?)+", "", chunk)
    for am in AMOUNT_TOKEN_RE.finditer(chunk):
        token = am.group(0)
        if _is_percent_token(chunk, am.end()):
            continue
        val = _num(token)
        if _looks_like_account_number(val, token):
            continue
        if _is_price_amount(val, token, role):
            return float(val), token
    return None


def _auto_flag(name: str, default: str = "1") -> bool:
    from config.settings import settings

    raw = str(getattr(settings, name, default) or default).strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def _label_line_is_header(text: str, match_start: int, label: str) -> bool:
    """表头行「合计金额 合计税额 价税合计」后面才是数字，禁止跨行抓第一个数。

    若本行已经出现像样金额（不是物料编码），则按同行抓数，不当纯表头。
    """
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_start)
    line = text[line_start : line_end if line_end >= 0 else None]
    siblings = [lab for lab, _role in _LABEL_ROLE_PATTERNS if lab != label and lab in line]
    if len(siblings) < 1:
        return False
    for am in AMOUNT_TOKEN_RE.finditer(line):
        token = am.group(0)
        if _is_percent_token(line, am.end()):
            continue
        if _looks_like_account_number(_num(token), token):
            continue
        if _is_price_amount(_num(token), token, "tax_exclusive"):
            return False
    return True


def _in_html_detail_table_header(text: str, match_start: int) -> bool:
    """HTML 明细表头（项目名称/数量/单价/不含税金额…）不能当单据级金额标签。"""
    return in_html_detail_table_header(text, match_start)


def _collect_header_row_amounts(text: str) -> list[dict[str, Any]]:
    """表头行与下一行数字按从左到右对齐（仅发票/订单合计行）。"""
    lines = to_ascii_digits(text).splitlines()
    found: list[dict[str, Any]] = []
    patterns = sorted(_LABEL_ROLE_PATTERNS, key=lambda x: len(x[0]), reverse=True)
    footer_labels = frozenset(
        {"合计金额", "合计税额", "价税合计", "税额合计", "合计（不含税）", "合计不含税"}
    )
    for i, line in enumerate(lines[:-1]):
        hits: list[tuple[int, str, str]] = []
        used_spans: list[tuple[int, int]] = []
        for lab, role in patterns:
            pos = 0
            while True:
                j = line.find(lab, pos)
                if j < 0:
                    break
                span = (j, j + len(lab))
                if any(not (span[1] <= a or span[0] >= b) for a, b in used_spans):
                    pos = j + 1
                    continue
                hits.append((j, lab, role))
                used_spans.append(span)
                break
        if len(hits) < 2:
            continue
        if any(tok in line for tok in ("数量", "单价", "税率", "规格型号", "规格")):
            # 明细表头列数与「合计金额/税额/价税」不同，禁止按从左到右硬对齐
            continue
        hits.sort(key=lambda x: x[0])
        labs = {h[1] for h in hits}
        if not labs.intersection(footer_labels):
            continue
        if any(
            lab in {"授信额度", "授信上限", "可用额度", "可用授信", "信用额度", "内部授信"}
            for lab in labs
        ):
            continue
        next_line = lines[i + 1]
        if re.search(r"[元折扣商品率为]", next_line):
            continue
        if "价税合计" in labs and ("合计税额" in labs or "税额合计" in labs) and "合计金额" in labs:
            hits = [
                (pos, lab, "tax_exclusive" if lab == "合计金额" else role)
                for pos, lab, role in hits
            ]
        nums = list(AMOUNT_TOKEN_RE.finditer(lines[i + 1]))
        if len(nums) < 2:
            continue
        for (_pos, lab, role), nm in zip(hits, nums):
            val = _num(nm.group(0))
            if not _is_price_amount(val, nm.group(0), role):
                continue
            found.append(
                {
                    "candidate_id": f"H{len(found) + 1}",
                    "value": _round2(val),
                    "raw_value": f"{lab} {nm.group(0)}",
                    "currency": "CNY",
                    "tax_basis": role,
                    "role": role,
                    "label": lab,
                    "source_type": "ocr_rule",
                    "evidence": {"raw_text": f"{line}\n{lines[i + 1]}"[:200], "page": 1},
                    "validation": [],
                }
            )
    return found


def _collect_labeled_candidates(ocr_text: str) -> list[dict[str, Any]]:
    text = to_ascii_digits(str(ocr_text or ""))
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for row in _collect_header_row_amounts(text):
        key = (str(row.get("label") or ""), float(row["value"]))
        if key in seen:
            continue
        seen.add(key)
        found.append(row)
    # Longer labels first to avoid「税额」吃掉「税额合计」
    patterns = sorted(_LABEL_ROLE_PATTERNS, key=lambda x: len(x[0]), reverse=True)
    for label, role in patterns:
        for m in re.finditer(rf"{re.escape(label)}\s*[:：]?", text, flags=re.I):
            if _in_html_detail_table_header(text, m.start()):
                continue
            if _label_glued_to_unit_or_rate(text, m.end()):
                continue
            if _label_line_is_header(text, m.start(), label):
                continue
            picked = _first_price_after(text, m.end(), role)
            if picked is None:
                continue
            val, token = picked
            key = (label, _round2(val))
            if key in seen:
                continue
            seen.add(key)
            snippet = f"{label} {token}".strip()[:120]
            found.append(
                {
                    "candidate_id": f"C{len(found) + 1}",
                    "value": _round2(val),
                    "raw_value": snippet,
                    "currency": "CNY",
                    "tax_basis": role,
                    "role": role,
                    "label": label,
                    "source_type": "ocr_rule",
                    "evidence": {"raw_text": snippet[:200], "page": 1},
                    "validation": [],
                }
            )
    # Also honor TOTAL_AMOUNT_LABELS from amount_resolve
    for label in TOTAL_AMOUNT_LABELS:
        lab = str(label)
        if any(lab == x[0] for x in patterns):
            continue
        for m in re.finditer(
            rf"{re.escape(lab)}\s*[:：]?\s*(?:[（(]?\s*小写[）)]?)?\s*[¥￥$]?\s*({AMOUNT_TOKEN_RE.pattern})",
            text,
            flags=re.I,
        ):
            if _label_line_is_header(text, m.start(), lab):
                continue
            val = _num(m.group(1))
            if not _is_price_amount(val, m.group(1), "tax_inclusive"):
                continue
            key = (lab, _round2(val))
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "candidate_id": f"C{len(found) + 1}",
                    "value": _round2(val),
                    "raw_value": m.group(0).strip()[:120],
                    "currency": "CNY",
                    "tax_basis": "tax_inclusive",
                    "role": "tax_inclusive",
                    "label": lab,
                    "source_type": "ocr_rule",
                    "evidence": {"raw_text": m.group(0).strip()[:200], "page": 1},
                    "validation": [],
                }
            )
    return found


def _field_candidate(
    *,
    candidate_id: str,
    value: float,
    label: str,
    tax_basis: str,
    source_type: str,
    raw_value: Any = None,
    role: str | None = None,
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "value": _round2(value),
        "raw_value": str(raw_value if raw_value is not None else value),
        "currency": "CNY",
        "tax_basis": tax_basis,
        "role": role or tax_basis,
        "label": label,
        "source_type": source_type,
        "evidence": {},
        "validation": [],
    }


def _prior_closed(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in item.get(AMBIGUITY_KEY) or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").upper()
        if status in CLOSED_STATUSES and status != "SYSTEM_CONSISTENT":
            out[str(row.get("field_key") or "")] = row
    return out


def _dedupe_candidates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, float]] = set()
    for c in cands:
        if not isinstance(c, dict):
            continue
        val = _num(c.get("value"))
        if val is None:
            continue
        key = (str(c.get("label") or c.get("role") or ""), _round2(val))
        if key in seen:
            continue
        seen.add(key)
        row = dict(c)
        row["value"] = _round2(val)
        out.append(row)
    for i, c in enumerate(out, start=1):
        c["candidate_id"] = f"C{i}"
    return out


_SOURCE_PRIORITY = {
    "field": 0,
    "vat_specialist": 1,
    "calc": 2,
    "ocr_rule": 3,
}


def _prune_labeled_noise(labeled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去掉明细行上的小金额（如行税额 300），保留单据级合计。"""
    by_role: dict[str, list[float]] = {}
    for c in labeled:
        role = str(c.get("role") or "")
        val = _num(c.get("value"))
        if val is None:
            continue
        by_role.setdefault(role, []).append(abs(float(val)))
    drop: set[tuple[str, float]] = set()
    for role in ("tax_exclusive", "tax_only", "tax_inclusive"):
        vals = sorted({v for v in by_role.get(role) or []}, reverse=True)
        if len(vals) < 2:
            continue
        top = vals[0]
        if top < 200:
            continue
        floor = max(top * 0.2, 1000.0)
        for v in vals:
            if v < floor:
                drop.add((role, _round2(v)))
    out: list[dict[str, Any]] = []
    for c in labeled:
        role = str(c.get("role") or "")
        val = _num(c.get("value"))
        if val is None:
            continue
        if (role, _round2(abs(float(val)))) in drop:
            continue
        out.append(c)
    return out


def _dedupe_candidates_by_value(
    cands: list[dict[str, Any]],
    *,
    max_count: int = 5,
) -> list[dict[str, Any]]:
    best: dict[float, dict[str, Any]] = {}
    for c in cands:
        if not isinstance(c, dict):
            continue
        val = _num(c.get("value"))
        if val is None:
            continue
        key = _round2(val)
        prev = best.get(key)
        if prev is None:
            best[key] = dict(c)
            continue
        src = str(c.get("source_type") or "ocr_rule")
        prev_src = str(prev.get("source_type") or "ocr_rule")
        if _SOURCE_PRIORITY.get(src, 9) < _SOURCE_PRIORITY.get(prev_src, 9):
            best[key] = dict(c)
    ordered = sorted(
        best.values(),
        key=lambda x: (
            _SOURCE_PRIORITY.get(str(x.get("source_type") or "ocr_rule"), 9),
            -abs(float(x.get("value") or 0)),
        ),
    )
    out = ordered[:max_count]
    for i, c in enumerate(out, start=1):
        c["candidate_id"] = f"C{i}"
    return out


def _reuse_ai_recommendation(
    prior: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(prior, dict):
        return None
    rec_id = str(prior.get("candidate_id") or "").strip()
    if rec_id and any(str(c.get("candidate_id") or "") == rec_id for c in candidates if isinstance(c, dict)):
        return dict(prior)
    pref = prior.get("recommended_value")
    if pref is not None:
        try:
            want = float(pref)
        except (TypeError, ValueError):
            want = None
        if want is not None:
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                try:
                    val = float(c.get("value"))
                except (TypeError, ValueError):
                    continue
                if abs(val - want) <= 0.05:
                    out = dict(prior)
                    out["candidate_id"] = str(c.get("candidate_id") or "")
                    out["recommended_value"] = want
                    return out
    return None


def _filter_candidates_for_field(
    candidates: list[dict[str, Any]],
    *,
    field_key: str,
    fields: dict[str, Any],
    qty: float | None,
) -> list[dict[str, Any]]:
    amount = _num(fields.get("amount"))
    tax = _num(fields.get("taxAmount"))
    total = _num(fields.get("totalAmount"))
    cur = _num(fields.get(field_key))
    want_basis = str(amount_field_spec(field_key).get("tax_basis") or "")
    out: list[dict[str, Any]] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        val = _num(c.get("value"))
        if val is None:
            continue
        role = str(c.get("role") or c.get("tax_basis") or "")
        src = str(c.get("source_type") or "")
        if role in {"credit_limit", "credit_available", "discount"}:
            continue
        if field_key == "amount" and role not in {want_basis, "pre_discount", "tax_exclusive"} and src != "field":
            continue
        if field_key == "taxAmount" and role not in {"tax_only", want_basis} and src not in {"field", "calc", "vat_specialist"}:
            continue
        if field_key == "totalAmount" and role not in {"tax_inclusive", "unknown_total", want_basis} and src not in {
            "field",
            "calc",
            "vat_specialist",
        }:
            continue
        if src != "calc" and not _plausible_competing_amount(
            float(val),
            current=cur,
            quantity=qty,
            amount=amount,
            total=total,
        ):
            continue
        out.append(c)
    return _dedupe_candidates_by_value(out, max_count=5)


def _build_field_candidates(
    *,
    field_key: str,
    labeled: list[dict[str, Any]],
    fields: dict[str, Any],
) -> list[dict[str, Any]]:
    spec = amount_field_spec(field_key)
    want_basis = str(spec.get("tax_basis") or "unknown")
    primary: list[dict[str, Any]] = []
    interference: list[dict[str, Any]] = []
    for c in labeled:
        role = str(c.get("role") or c.get("tax_basis") or "")
        mapped = _ROLE_TO_FIELD.get(role)
        if mapped == field_key or (field_key == "totalAmount" and role == "unknown_total"):
            primary.append(dict(c))
        elif role in _INTERFERENCE_ROLES or (mapped and mapped != field_key):
            row = dict(c)
            row["role"] = role or "interference"
            interference.append(row)

    cur = _num(fields.get(field_key))
    if (
        cur is not None
        and _is_price_amount(cur, str(fields.get(field_key) or cur), want_basis)
        and not any(abs(float(c["value"]) - cur) < 0.009 for c in primary)
    ):
        primary.append(
            _field_candidate(
                candidate_id="F1",
                value=cur,
                label=f"字段·{field_label(field_key)}",
                tax_basis=want_basis,
                source_type="field",
                raw_value=fields.get(field_key),
                role=want_basis,
            )
        )

    # Cap interference so prompt stays small, prefer credit / pre-discount
    interference_sorted = sorted(
        interference,
        key=lambda x: (
            0 if str(x.get("role")) in {"credit_limit", "credit_available", "pre_discount"} else 1,
            -abs(float(x.get("value") or 0)),
        ),
    )[:2]
    merged = list(primary)
    if field_key == "amount" and want_basis == "tax_exclusive":
        for row in interference_sorted:
            if str(row.get("role") or "") == "pre_discount":
                merged.append(row)
                break
    return _dedupe_candidates_by_value(merged, max_count=5)


def scan_document(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan one document and rewrite ``_amount_ambiguities`` (preserving human closes)."""
    if not isinstance(item, dict):
        return []
    fields = effective_fields(item) if item.get("_field_meta") else dict(item.get("fields") or {})
    ocr_text = str(item.get("raw_text") or item.get("ocr_text") or "")
    doc_type = str(item.get("doc_type") or "").lower()
    file_name = str(item.get("file_name") or "")
    prior = _prior_closed(item)
    prior_open: dict[str, dict[str, Any]] = {}
    for row in item.get(AMBIGUITY_KEY) or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").upper()
        if status not in OPEN_STATUSES:
            continue
        fk = str(row.get("field_key") or "")
        if fk:
            prior_open[fk] = row

    labeled = _prune_labeled_noise(_collect_labeled_candidates(ocr_text))
    amount = _num(fields.get("amount"))
    tax = _num(fields.get("taxAmount"))
    total = _num(fields.get("totalAmount"))

    recon_ok: bool | None = None
    if amount is not None and tax is not None and total is not None:
        recon_ok = abs((amount + tax) - total) <= 0.01
    elif amount is not None and total is not None and tax is None and total > amount + 0.009:
        # 发票常漏税额：未税+价税已齐则视为自洽，不拿税率/数量冒充多金额
        recon_ok = True
    elif (
        amount is not None
        and total is not None
        and abs(amount - total) <= 0.01
        and (tax is None or abs(tax) <= 0.009)
    ):
        # 出口零税 / 无税额：未税=价税视为自洽
        recon_ok = True

    qty = _num(fields.get("quantity"))

    interference_present = any(
        str(c.get("role") or "") in _INTERFERENCE_ROLES for c in labeled
    )
    role_collision = (
        amount is not None
        and total is not None
        and abs(amount - total) <= 0.01
        and tax is not None
        and abs(tax) > 0.009
    )

    # Per-field: MULTIPLE only among candidates that belong to this field's role
    field_open: dict[str, list[str]] = {k: [] for k in TARGET_FIELDS}
    for field_key in TARGET_FIELDS:
        cands = _build_field_candidates(field_key=field_key, labeled=labeled, fields=fields)
        own = []
        for c in cands:
            role = str(c.get("role") or c.get("tax_basis") or "")
            if role in _INTERFERENCE_ROLES:
                continue
            mapped = _ROLE_TO_FIELD.get(role)
            if mapped == field_key or (
                field_key == "totalAmount" and role in {"tax_inclusive", "unknown_total"}
            ):
                own.append(c)
            elif str(c.get("source_type") or "") == "field" and role == str(
                amount_field_spec(field_key).get("tax_basis") or ""
            ):
                own.append(c)
            elif str(c.get("source_type") or "") == "field" and not mapped:
                # 「字段·xxx」候选
                own.append(c)
        distinct = {_round2(float(c["value"])) for c in own}
        if len(distinct) >= 2:
            cur = _num(fields.get(field_key))
            plausible = {
                v
                for v in distinct
                if _plausible_competing_amount(
                    v,
                    current=cur,
                    quantity=qty,
                    amount=amount,
                    total=total,
                )
            }
            if recon_ok:
                if cur is not None:
                    others = {
                        v
                        for v in plausible
                        if abs(v - _round2(cur)) > 0.05
                        and not _is_amount_fragment(min(v, cur), max(v, cur))
                    }
                    distinct = { _round2(cur) } | others if others else { _round2(cur) }
                else:
                    distinct = plausible
            else:
                distinct = plausible or distinct
            if len(distinct) >= 2:
                field_open[field_key].append("MULTIPLE_CANDIDATES")

    global_triggers: list[str] = []
    if recon_ok is False:
        global_triggers.append("RECONCILIATION_FAILED")
    if role_collision:
        global_triggers.append("ROLE_COLLISION")
    if interference_present and doc_type in {"invoice", "other", ""}:
        # Soft: only force review when current fields missing or collision/recon
        if recon_ok is False or role_collision or amount is None or total is None:
            global_triggers.append("INTERFERENCE_NEARBY")
    if doc_type == "invoice" and total is None and not any(
        str(c.get("role")) == "tax_inclusive" for c in labeled
    ):
        global_triggers.append("MISSING_TOTAL")

    # Decide which fields need cards
    need_fields: list[str] = []
    for field_key in TARGET_FIELDS:
        triggers = list(field_open[field_key])
        if "MISSING_TOTAL" in global_triggers and field_key == "totalAmount":
            triggers.append("MISSING_TOTAL")
        if "RECONCILIATION_FAILED" in global_triggers and field_key == "amount":
            triggers.append("RECONCILIATION_FAILED")
        if "ROLE_COLLISION" in global_triggers and field_open[field_key]:
            triggers.append("ROLE_COLLISION")
        if "INTERFERENCE_NEARBY" in global_triggers and "MULTIPLE_CANDIDATES" in field_open[field_key]:
            triggers.append("INTERFERENCE_NEARBY")
        triggers = list(dict.fromkeys(triggers))
        if triggers:
            need_fields.append(field_key)
            field_open[field_key] = triggers

    if not need_fields:
        # Quiet success
        item[AMBIGUITY_KEY] = [prior[k] for k in TARGET_FIELDS if k in prior] or []
        if not item[AMBIGUITY_KEY]:
            item[AMBIGUITY_KEY] = []
        return []

    rows: list[dict[str, Any]] = []
    for field_key in TARGET_FIELDS:
        if field_key in prior:
            rows.append(prior[field_key])
            continue
        if field_key not in need_fields:
            continue
        candidates = _filter_candidates_for_field(
            _build_field_candidates(field_key=field_key, labeled=labeled, fields=fields),
            field_key=field_key,
            fields=fields,
            qty=qty,
        )
        if recon_ok is False and field_key == "amount" and total is not None and tax is not None:
            inferred = _round2(total - tax)
            if inferred > 0 and not any(abs(float(c["value"]) - inferred) < 0.009 for c in candidates):
                candidates.append(
                    _field_candidate(
                        candidate_id=f"C{len(candidates) + 1}",
                        value=inferred,
                        label="推算·价税合计−税额",
                        tax_basis="tax_exclusive",
                        source_type="calc",
                        raw_value=f"{total}-{tax}",
                        role="tax_exclusive",
                    )
                )
                candidates = _dedupe_candidates_by_value(candidates, max_count=5)
        if recon_ok is False and field_key == "totalAmount" and amount is not None and tax is not None:
            calc = _round2(amount + tax)
            total_known = _num(fields.get("totalAmount"))
            skip_calc = (
                total_known is not None
                and abs(calc - float(total_known)) > max(1.0, abs(float(total_known)) * 0.01)
            )
            if not skip_calc and not any(abs(float(c["value"]) - calc) < 0.009 for c in candidates):
                candidates.append(
                    _field_candidate(
                        candidate_id=f"C{len(candidates) + 1}",
                        value=calc,
                        label="推算·未税+税额",
                        tax_basis="tax_inclusive",
                        source_type="calc",
                        raw_value=f"{amount}+{tax}",
                        role="tax_inclusive",
                    )
                )
                candidates = _dedupe_candidates_by_value(candidates, max_count=5)

        cur = _num(fields.get(field_key))
        if (
            len(candidates) == 1
            and cur is not None
            and field_key != "amount"
        ):
            only = _num(candidates[0].get("value"))
            if only is not None and abs(only - cur) <= 0.05:
                continue

        status = "NEEDS_REVIEW"
        if "MISSING_TOTAL" in field_open[field_key] and not candidates:
            status = "INSUFFICIENT_EVIDENCE"

        prior_row = prior_open.get(field_key) or {}
        rows.append(
            {
                "ambiguity_id": str(prior_row.get("ambiguity_id") or f"amt-{file_name}-{field_key}-{uuid4().hex[:8]}"),
                "file_name": file_name,
                "field_key": field_key,
                "field_name": amount_field_spec(field_key).get("field_name") or field_label(field_key),
                "status": status,
                "trigger_reasons": field_open[field_key],
                "candidates": candidates,
                "ai_recommendation": _reuse_ai_recommendation(
                    prior_row.get("ai_recommendation"),
                    candidates,
                ),
                "vision_attempted": bool(prior_row.get("vision_attempted")),
                "human_decision": prior_row.get("human_decision"),
                "created_at": str(prior_row.get("created_at") or _utc_now()),
            }
        )

    item[AMBIGUITY_KEY] = rows
    return [r for r in rows if str(r.get("status") or "").upper() in OPEN_STATUSES]


def scan_job_documents(job: dict[str, Any], *, chain_id: str | None = None) -> list[dict[str, Any]]:
    classified = list(job.get("classified") or [])
    if chain_id:
        touch = {str(d.get("file_name") or "") for d in docs_for_chain(classified, chain_id)}
    else:
        touch = None
    opened: list[dict[str, Any]] = []
    for item in classified:
        if not isinstance(item, dict):
            continue
        if touch is not None and str(item.get("file_name") or "") not in touch:
            continue
        for row in scan_document(item):
            if str(row.get("status") or "").upper() in OPEN_STATUSES:
                opened.append(row)
    job["classified"] = classified
    return opened


def list_open_ambiguities(job: dict[str, Any], *, chain_id: str | None = None) -> list[dict[str, Any]]:
    """只读已落库的歧义行；不在此重扫（重扫交给识别后 / 显式 scan /chains）。"""
    classified = list(job.get("classified") or [])
    if chain_id is None and is_gospd_mode(job):
        chain_id = resolve_active_chain_id(job) or None
    if chain_id:
        allow = {str(d.get("file_name") or "") for d in docs_for_chain(classified, chain_id)}
    else:
        allow = None
    out: list[dict[str, Any]] = []
    for item in classified:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file_name") or "")
        if allow is not None and name not in allow:
            continue
        for row in item.get(AMBIGUITY_KEY) or []:
            if isinstance(row, dict) and str(row.get("status") or "").upper() in OPEN_STATUSES:
                out.append(dict(row))
    return out


def find_ambiguity(job: dict[str, Any], ambiguity_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    want = str(ambiguity_id or "").strip()
    for item in job.get("classified") or []:
        if not isinstance(item, dict):
            continue
        for row in item.get(AMBIGUITY_KEY) or []:
            if isinstance(row, dict) and str(row.get("ambiguity_id") or "") == want:
                return item, row
    return None


def apply_ai_recommendation(item: dict[str, Any], ambiguity_id: str, review: dict[str, Any]) -> dict[str, Any]:
    rows = list(item.get(AMBIGUITY_KEY) or [])
    for row in rows:
        if not isinstance(row, dict) or str(row.get("ambiguity_id") or "") != ambiguity_id:
            continue
        if str(row.get("status") or "").upper() not in OPEN_STATUSES:
            return row
        rec_id = review.get("recommended_candidate_id")
        rec_val = None
        for c in row.get("candidates") or []:
            if isinstance(c, dict) and str(c.get("candidate_id") or "") == str(rec_id or ""):
                rec_val = _num(c.get("value"))
                rec_token = str(c.get("raw_value") or c.get("value") or "")
                break
        else:
            rec_token = ""
        fk = str(row.get("field_key") or "")
        if fk in TARGET_FIELDS and rec_id and not _is_price_amount(rec_val, rec_token, "tax_exclusive"):
            rec_id = None
            review = dict(review)
            review["review_status"] = "NEEDS_REVIEW"
            note = "负值或物料编码不能作为金额推荐，已作废。"
            review["reason"] = ((str(review.get("reason") or "").strip() + "；") if review.get("reason") else "") + note
        row["ai_recommendation"] = {
            "candidate_id": rec_id,
            "reason": review.get("reason"),
            "confidence": review.get("confidence"),
            "model": review.get("model"),
            "review_status": review.get("review_status"),
            "provider": review.get("provider"),
            "prompt_version": review.get("prompt_version"),
        }
        item[AMBIGUITY_KEY] = rows
        return row
    raise KeyError(ambiguity_id)


def merge_specialist_candidates(item: dict[str, Any], specialist: dict[str, Any]) -> None:
    """Inject Baidu VAT invoice amounts into open ambiguity candidate lists."""
    if not isinstance(specialist, dict) or not specialist.get("ok"):
        return
    rows = list(item.get(AMBIGUITY_KEY) or [])
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").upper() not in OPEN_STATUSES:
            continue
        fk = str(row.get("field_key") or "")
        val = _num(specialist.get(fk))
        if val is None:
            continue
        cands = list(row.get("candidates") or [])
        if any(abs(float(c.get("value") or 0) - val) < 0.009 for c in cands if isinstance(c, dict)):
            # Tag matching candidate as specialist
            for c in cands:
                if isinstance(c, dict) and abs(float(c.get("value") or 0) - val) < 0.009:
                    c["source_type"] = c.get("source_type") or "vat_specialist"
                    c["label"] = c.get("label") or f"增值税识别·{field_label(fk)}"
            row["candidates"] = cands
            changed = True
            continue
        cands.append(
            _field_candidate(
                candidate_id=f"V{len(cands) + 1}",
                value=val,
                label=f"增值税识别·{field_label(fk)}",
                tax_basis=str(amount_field_spec(fk).get("tax_basis") or "unknown"),
                source_type="vat_specialist",
                raw_value=val,
                role=str(amount_field_spec(fk).get("tax_basis") or "unknown"),
            )
        )
        row["candidates"] = _dedupe_candidates_by_value(cands, max_count=5)
        changed = True
    if changed:
        item[AMBIGUITY_KEY] = rows


def enrich_document_ambiguities(
    item: dict[str, Any],
    *,
    auto_vat: bool | None = None,
    auto_vision: bool | None = None,
) -> dict[str, Any]:
    """Optional specialist + vision enrichment for open cards. Advisory only."""
    summary: dict[str, Any] = {
        "opened": 0,
        "vat": None,
        "vision": [],
        "errors": [],
    }
    opened = [
        r
        for r in (item.get(AMBIGUITY_KEY) or [])
        if isinstance(r, dict) and str(r.get("status") or "").upper() in OPEN_STATUSES
    ]
    summary["opened"] = len(opened)
    if not opened:
        return summary
    if item.get("demo_ocr_cache"):
        return summary

    do_vat = _auto_flag("AMOUNT_AMBIGUITY_AUTO_VAT") if auto_vat is None else bool(auto_vat)
    do_vision = _auto_flag("AMOUNT_AMBIGUITY_AUTO_VISION") if auto_vision is None else bool(auto_vision)

    path = Path(str(item.get("path") or ""))
    ocr_path = str(item.get("ocr_image_path") or "").strip()
    if ocr_path and Path(ocr_path).is_file():
        path = Path(ocr_path)
    image_bytes: bytes | None = None
    if path.is_file():
        try:
            from src.ui.preview_capture import render_preview_page

            image_bytes, _meta = render_preview_page(path, page_index=0)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"preview:{exc}")

    if do_vat and str(item.get("doc_type") or "").lower() == "invoice" and image_bytes:
        try:
            from src.llm.baidu_vat_invoice import extract_vat_invoice_amounts, vat_invoice_status

            st = vat_invoice_status()
            if st.get("configured") and st.get("enabled"):
                result = extract_vat_invoice_amounts(image_bytes)
                summary["vat"] = {
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "error_code": result.get("error_code"),
                    "amount": result.get("amount"),
                    "taxAmount": result.get("taxAmount"),
                    "totalAmount": result.get("totalAmount"),
                }
                if result.get("ok"):
                    merge_specialist_candidates(item, result)
                elif result.get("error_code") in {6, 17, 18, 19, 100, 110, 111}:
                    summary["errors"].append(
                        f"vat_permission:{result.get('error_code')}:{result.get('error')}"
                    )
            else:
                summary["vat"] = {"ok": False, "error": "not_configured", "configured": False}
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"vat:{exc}")
            summary["vat"] = {"ok": False, "error": str(exc)}

    if do_vision and image_bytes:
        try:
            from src.llm.qianfan_vision import review_amount_candidates, vision_status

            vs = vision_status()
            if not vs.get("configured"):
                summary["errors"].append("vision_not_configured")
            else:
                for row in list(item.get(AMBIGUITY_KEY) or []):
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("status") or "").upper() not in OPEN_STATUSES:
                        continue
                    if row.get("ai_recommendation") or row.get("vision_attempted"):
                        continue
                    cands = list(row.get("candidates") or [])
                    if not cands:
                        continue
                    try:
                        review = review_amount_candidates(
                            image_png=image_bytes,
                            field_key=str(row.get("field_key") or "totalAmount"),
                            candidates=cands,
                            ocr_text=str(item.get("raw_text") or ""),
                        )
                        apply_ai_recommendation(item, str(row.get("ambiguity_id")), review)
                        row["vision_attempted"] = True
                        summary["vision"].append(
                            {
                                "ambiguity_id": row.get("ambiguity_id"),
                                "field_key": row.get("field_key"),
                                "recommended_candidate_id": review.get("recommended_candidate_id"),
                                "review_status": review.get("review_status"),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        row["vision_attempted"] = True
                        summary["errors"].append(f"vision:{row.get('field_key')}:{exc}")
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"vision:{exc}")

    return summary


def enrich_job_ambiguities(
    job: dict[str, Any],
    *,
    chain_id: str | None = None,
) -> dict[str, Any]:
    classified = list(job.get("classified") or [])
    if chain_id:
        allow = {str(d.get("file_name") or "") for d in docs_for_chain(classified, chain_id)}
    else:
        allow = None
    docs = 0
    combined: dict[str, Any] = {"docs": 0, "opened": 0, "vision": [], "vat": [], "errors": []}
    for item in classified:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file_name") or "")
        if allow is not None and name not in allow:
            continue
        opened = [
            r
            for r in (item.get(AMBIGUITY_KEY) or [])
            if isinstance(r, dict) and str(r.get("status") or "").upper() in OPEN_STATUSES
        ]
        if not opened:
            continue
        docs += 1
        summary = enrich_document_ambiguities(item)
        combined["opened"] += int(summary.get("opened") or 0)
        combined["vision"].extend(summary.get("vision") or [])
        if summary.get("vat") is not None:
            combined["vat"].append({"file_name": name, **(summary.get("vat") or {})})
        combined["errors"].extend(summary.get("errors") or [])
    combined["docs"] = docs
    job["classified"] = classified
    return combined


def decide_ambiguity(
    item: dict[str, Any],
    ambiguity_id: str,
    *,
    decision: str,
    candidate_id: str | None = None,
    value: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    """Apply human decision and accept the field. Mutates ``item``."""
    hit = None
    rows = list(item.get(AMBIGUITY_KEY) or [])
    for row in rows:
        if isinstance(row, dict) and str(row.get("ambiguity_id") or "") == ambiguity_id:
            hit = row
            break
    if hit is None:
        raise KeyError(f"ambiguity not found: {ambiguity_id}")
    field_key = str(hit.get("field_key") or "totalAmount")
    decision_u = str(decision or "").upper()
    if decision_u == "ACCEPT_CANDIDATE":
        cand = next(
            (
                c
                for c in (hit.get("candidates") or [])
                if isinstance(c, dict) and str(c.get("candidate_id") or "") == str(candidate_id or "")
            ),
            None,
        )
        if not cand:
            raise ValueError("candidate_id 无效或不在候选列表中")
        raw_hl = cand.get("raw_value") or (cand.get("evidence") or {}).get("raw_text")
        accept_field(
            item,
            field_key,
            cand.get("value"),
            source="amount_ambiguity_candidate",
            highlight_text=raw_hl if raw_hl not in (None, "") else cand.get("value"),
        )
        hit["status"] = "CONFIRMED"
        hit["human_decision"] = {
            "decision": decision_u,
            "candidate_id": candidate_id,
            "value": cand.get("value"),
            "raw_value": raw_hl,
            "reason": reason or "采用候选",
            "at": _utc_now(),
        }
    elif decision_u == "MANUAL_VALUE":
        if value is None or str(value).strip() == "":
            raise ValueError("手工录入值不能为空")
        parsed = _num(value)
        if parsed is None:
            raise ValueError("手工录入值不是有效金额")
        accept_field(
            item,
            field_key,
            parsed,
            source="amount_ambiguity_manual",
            highlight_text=str(value).strip(),
        )
        hit["status"] = "CONFIRMED"
        hit["human_decision"] = {
            "decision": decision_u,
            "value": parsed,
            "reason": reason or "手工录入",
            "at": _utc_now(),
        }
    elif decision_u == "DEFER":
        hit["human_decision"] = {"decision": decision_u, "reason": reason or "暂存", "at": _utc_now()}
    else:
        raise ValueError("decision 须为 ACCEPT_CANDIDATE / MANUAL_VALUE / DEFER")
    item[AMBIGUITY_KEY] = rows
    return hit

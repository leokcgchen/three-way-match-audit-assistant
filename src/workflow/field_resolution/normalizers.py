"""Deterministic, auditable field normalizers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Any


_PUNCTUATION_RE = re.compile(r"[\s,，。；;：:、·•()（）\[\]【】{}<>《》\-_/\\]+")
_CURRENCY_RE = re.compile(r"(?:人民币|RMB|CNY|CN¥|￥|¥|USD|US\$|\$)", re.IGNORECASE)


def _compact(value: Any) -> str:
    return _PUNCTUATION_RE.sub("", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def normalize_legal_entity(value: Any) -> str:
    return _compact(value)


def normalize_address(value: Any) -> str:
    return _compact(value)


def normalize_goods(value: Any) -> str:
    return _compact(value)


def normalize_identifier(value: Any) -> str:
    return _compact(value).upper()


def normalize_currency(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    aliases = {"RMB": "CNY", "人民币": "CNY", "￥": "CNY", "CN¥": "CNY", "US$": "USD", "$": "USD"}
    return aliases.get(text, text)


def normalize_unit(value: Any) -> str:
    text = _compact(value)
    aliases = {
        "臺": "台",
        "台": "台",
        "pcs": "件",
        "pc": "件",
        "piece": "件",
        "pieces": "件",
        "个": "件",
        "件": "件",
        "套": "套",
        "kg": "kg",
        "公斤": "kg",
    }
    return aliases.get(text, text)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = _CURRENCY_RE.sub("", text).replace(",", "").replace("，", "").strip("() ")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = Decimal(match.group(0))
    except InvalidOperation:
        return None
    return -number if negative and number > 0 else number


__all__ = [
    "normalize_address",
    "normalize_currency",
    "normalize_goods",
    "normalize_identifier",
    "normalize_legal_entity",
    "normalize_unit",
    "parse_decimal",
]

"""供应商名称归一化（三单匹配用）。"""

from __future__ import annotations

import re
import unicodedata


_PREFIXES = (
    "供应商名称",
    "供应商",
    "销售方名称",
    "销售方",
    "卖方",
    "乙方",
    "供方",
    "供货单位",
    "seller",
    "supplier",
)

# 括号内常见：纳税人识别号、地址、电话等
_PAREN_PATTERNS = (
    r"[（(【\[][^）)\]】]*[）)\]】]",
    r"\d{15,20}",  # 税号
)


def _strip_parenthetical(text: str) -> str:
    result = text
    for _ in range(5):
        before = result
        for pat in _PAREN_PATTERNS:
            result = re.sub(pat, "", result)
        if result == before:
            break
    return result


def normalize_supplier_name(name: str) -> str:
    """去除空白、前缀、括号内容、全角符号后的小写归一化键。"""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = _strip_parenthetical(text)
    text = re.sub(r"\s+", "", text)
    for prefix in _PREFIXES:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix) :].lstrip(":： ")
    text = text.strip("：:()（）[]【】")
    return text.lower()


def suppliers_are_consistent(*names: str) -> bool:
    """
    判断多个供应商名称是否应视为一致。

    - 归一化后完全相同 → 一致
    - 去掉「有限公司/股份有限公司」等后缀后相同 → 一致
    - 较长名称包含较短名称（简称） → 一致
    """
    cleaned = [normalize_supplier_name(n) for n in names if normalize_supplier_name(n)]
    if not cleaned:
        return False
    if len(set(cleaned)) == 1:
        return True

    def _core(text: str) -> str:
        for suffix in (
            "有限责任公司",
            "股份有限公司",
            "有限公司",
            "集团公司",
            "集团",
        ):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return text

    cores = [_core(c) for c in cleaned]
    if len(set(cores)) == 1 and all(cores):
        return True

    base = max(cleaned, key=len)
    return all(c == base or c in base or base in c for c in cleaned)


def pick_canonical_supplier(*names: str) -> str:
    """从多个候选中取最长、最完整的供应商名称。"""
    originals = [str(n).strip() for n in names if str(n or "").strip()]
    if not originals:
        return "未知供应商"
    if suppliers_are_consistent(*originals):
        return max(originals, key=len)
    return originals[0]

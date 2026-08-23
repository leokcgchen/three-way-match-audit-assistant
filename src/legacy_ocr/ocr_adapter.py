"""Legacy OCR 适配器：千帆 PaddleOCR + LLM 字段提取（精简 Python 实现）。"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from config.settings import is_valid_api_credential, settings
from src.legacy_ocr.field_normalize import normalize_extracted_fields
from src.legacy_ocr.mock_data import mock_fields, mock_raw_text
from src.legacy_ocr.models import OcrResult
from src.utils.date_extractor import apply_receipt_date_fields
from src.utils.logger import logger

PADDLE_OCR_URL = "https://qianfan.baidubce.com/v2/ocr/paddleocr"
OAUTH_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
DOC_TYPE_ALIASES = {
    "order": "purchase_order",
    "purchase_order": "purchase_order",
    "po": "purchase_order",
    "receipt": "warehouse_receipt",
    "warehouse_receipt": "warehouse_receipt",
    "warehouse": "warehouse_receipt",
    "invoice": "invoice",
    "vat_invoice": "invoice",
    "contract": "contract",
    "agreement": "contract",
    "ht": "contract",
}


def _normalize_doc_type(document_type: str) -> str:
    key = (document_type or "").strip().lower()
    return DOC_TYPE_ALIASES.get(key, key or "other")


def _extract_pdf_text_layer(file_path: str) -> str:
    """从 PDF 内嵌文字层抽取文本（不依赖 OCR）。扫描件无文字层时返回空串。"""
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        return ""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        parts: List[str] = []
        with pdfplumber.open(path) as doc:
            for page in doc.pages:
                t = page.extract_text() or ""
                if t.strip():
                    parts.append(t.strip())
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 文字层抽取失败 path={} err={}", file_path, exc)
        return ""


# 各单据「语义关键字段」：缺了就触发 LLM 补抽（不靠堆正则认新表述）
_SEMANTIC_REQUIRED: Dict[str, tuple[str, ...]] = {
    "contract": (
        "paymentTerms",
        "controlTransferTerms",
        "contractNo",
        "totalAmount",
    ),
    "purchase_order": (
        "documentNo",
        "totalAmount",
        "quantity",
        "supplierName",
        "paymentTerms",
    ),
    "warehouse_receipt": (
        "deliveryDate",
        "acceptanceDate",
        "quantity",
        "documentNo",
    ),
    "invoice": ("invoiceNo", "totalAmount", "documentDate", "supplierName", "quantity"),
    "other": ("documentNo", "totalAmount"),
}

_PRESERVE_AI_KEYS = {
    "acceptanceDate",
    "receiptDateForCutoff",
    "deliveryDate",
    "paymentTerms",
    "settlementTerms",
    "transportTerms",
    "controlTransferTerms",
    "performanceObligations",
}


def _field_present(fields: Dict[str, Any], key: str) -> bool:
    val = fields.get(key)
    if val is None:
        return False
    if isinstance(val, (list, dict)) and not val:
        return False
    return bool(str(val).strip())


def _missing_semantic_fields(doc_type: str, fields: Dict[str, Any]) -> list[str]:
    required = _SEMANTIC_REQUIRED.get(doc_type, _SEMANTIC_REQUIRED["other"])
    missing = [k for k in required if not _field_present(fields, k)]
    # 签收单：到货/验收有其一即可不算双缺
    if doc_type == "warehouse_receipt":
        if _field_present(fields, "deliveryDate") or _field_present(
            fields, "acceptanceDate"
        ):
            missing = [k for k in missing if k not in {"deliveryDate", "acceptanceDate"}]
    if _field_present(fields, "paymentTerms") or _field_present(
        fields, "settlementTerms"
    ):
        missing = [k for k in missing if k not in {"paymentTerms", "settlementTerms"}]
    return missing


def _doc_type_label(doc_type: str) -> str:
    return {
        "purchase_order": "采购/销售订单",
        "warehouse_receipt": "入库单/发货单/验收签收单",
        "invoice": "增值税发票",
        "contract": "采购/销售合同",
        "other": "业务单据",
    }.get(doc_type, doc_type)


def _semantic_instructions(doc_type: str) -> str:
    common = """
【语义抽取原则——勿死记句式】
- 用审计语义理解，不要只匹配固定模板词。
- 付款账期：任何「何时付款/几日内付清/开票后、签收后、验收后、票到后 N 日」的完整表述 → paymentTerms
- 控制权：任何「验收合格/签收完成/货权/风险/所有权何时转移」 → controlTransferTerms
- 运输：承运、运费承担、交货地点、贸易术语 → transportTerms
- 履约义务：交付什么货物/服务 → performanceObligations（字符串数组）
- 日期：区分到货日 deliveryDate 与验收完成日 acceptanceDate；不要混用
- 金额/数量：从表格或正文理解后填写数字；缺失用 null（禁止用 0 冒充「未抽取」）
"""
    if doc_type == "warehouse_receipt":
        return (
            common
            + """
【签收/验收】
- deliveryDate=到货/发货日/到货日期；acceptanceDate=签收日期/验收完成日/「签收/验收完成日期」（截止性优先）
- 仅有到货日且写明 N 日验收期时，acceptanceDate=到货日+N天
- quantity 必填：表格「实收数量/合格数量/发运数量/发货数量/交货数量」任一有值即写入 quantity（优先实收→合格→发运/发货）；勿因没有光杆「数量」列就填 null
- documentNo：签收单号/验收单号/QS 号；buyerName 可取收货单位/购方名称
- totalAmount：签收单常无金额，有则填；无则 null（不要用 0 冒充）
"""
        )
    if doc_type == "contract":
        return (
            common
            + """
【合同】paymentTerms 必填（只要正文有付款期限）；controlTransferTerms 尽量完整摘录。
"""
        )
    if doc_type == "invoice":
        return (
            common
            + """
【发票】
- totalAmount←价税合计/含税合计；amount←不含税金额；quantity←数量列或明细合计
- invoiceNo/documentNo←发票号码；supplierName←销售方名称；buyerName←购买方名称
"""
        )
    if doc_type == "purchase_order":
        return (
            common
            + """
【订单】
- documentNo/orderNo←销售订单号/SO 号；quantity←数量（含明细合计）；totalAmount←价税/含税总金额
- supplierName←销方/卖方；buyerName←购方/客户；paymentTerms 若有则完整摘录
"""
        )
    return common


def _merge_ai_and_base(
    ai_fields: Dict[str, Any], base: Dict[str, Any]
) -> Dict[str, Any]:
    merged = dict(ai_fields or {})
    for k, v in (base or {}).items():
        if not v:
            continue
        if k in _PRESERVE_AI_KEYS and merged.get(k):
            continue
        if not merged.get(k):
            merged[k] = v
    return merged


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    stripped = raw.replace("\ufeff", "").replace("```json", "").replace("```", "").strip()
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    last_err: Optional[Exception] = None
    for cand in candidates:
        for version in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                data = json.loads(version)
                if isinstance(data, dict):
                    return data
            except Exception as exc:  # noqa: BLE001
                last_err = exc
    raise ValueError(f"LLM returned invalid JSON: {last_err}")


def extract_fields_heuristically(ocr_text: str) -> Dict[str, Any]:
    """无 LLM 时的高置信度兜底提取。"""

    def first(pattern: str) -> Optional[str]:
        m = re.search(pattern, ocr_text, flags=re.I)
        if not m:
            return None
        if m.lastindex:
            for i in range(1, m.lastindex + 1):
                g = m.group(i)
                if g and str(g).strip():
                    return str(g).strip()
        return m.group(0).strip() if m.group(0) else None

    def first_amount(pattern: str) -> Optional[str]:
        from src.legacy_ocr.amount_resolve import in_html_detail_table_header

        for m in re.finditer(pattern, ocr_text, flags=re.I):
            if in_html_detail_table_header(ocr_text, m.start()):
                continue
            if m.lastindex:
                for i in range(1, m.lastindex + 1):
                    g = m.group(i)
                    if g and str(g).strip():
                        return str(g).strip()
            if m.group(0):
                return m.group(0).strip()
        return None

    document_no = first(
        r"(?:发票号码|发票号|采购订单号|订单号|入库单号|Invoice\s*(?:No|Number)|PO\s*(?:No|Number))\s*[:：]?\s*([A-Z0-9][A-Z0-9_-]{3,})"
    )
    raw_date = first(
        r"(?:开票日期|发票日期|订单日期|单据日期|日期|Invoice\s*Date)\s*[:：]?\s*(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    )
    total_amount = first_amount(
        r"(?:订单价税合计|价税合计|含税总金额|合计金额|总金额|金额合计|Total\s*Amount|Grand\s*Total)"
        r"(?:\s*(?:人民币|RMB|CNY))?"
        r"\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
        r"|合\s*计\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
        r"|(?:合同总价|总价(?:\(含税\))?)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    net_amount = first_amount(
        r"(?:合计（不含税）|合计不含税|折后不含税金额|折后不含税|未税金额|不含税金额合计|不含税金额|未税小计)"
        r"(?:\s*(?:人民币|RMB|CNY))?"
        r"\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    tax_amount = first_amount(
        r"(?:税额|Tax\s*Amount)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    tax_rate = first(r"税率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%?")
    discount = first(
        r"(?:折扣率|折扣)\s*[:：]?\s*(\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*折)"
    )
    discount_amt = first(
        r"(?:折扣额|优惠金额)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    quantity = first(
        r"(?:实收数量|合格数量|发运数量|发货数量|交货数量|装船数量|提单数量|本批发货数量)"
        r"\s*(?:为|共|合计)?\s*[:：]?\s*([\d,]+(?:\.\d+)?)"
        r"|(?:^|[^\u4e00-\u9fff])数量\s*(?:为|共|合计)?\s*[:：]?\s*([\d,]+(?:\.\d+)?)"
        r"|Quantity\s*[:：]?\s*([\d,]+(?:\.\d+)?)"
    )
    # 表格行「357件 27.40」：优先取件数
    qty_piece = first(
        r"(\d{2,})\s*件\s+[\d,]+(?:\.\d{1,2})?"
        r"|(?:MAT|SKU|物料)[^\r\n]{0,48}?(\d{2,})\s*件"
    )
    if qty_piece:
        quantity = qty_piece
    elif quantity is None:
        quantity = first(
            r"(?:单位\s*)?件\s+([\d,]+(?:\.\d+)?)\s+(?:[\d,]+(?:\.\d+)?\s+){0,3}"
            r"(?:[\d,]+\.\d{2})"
        )
    # 避免把单价误当数量（如 27.40）
    if quantity is not None:
        try:
            qf = float(str(quantity).replace(",", ""))
            if qf < 100 and qty_piece:
                quantity = qty_piece
        except ValueError:
            pass
    # 避免「差异数量 0」盖过真实交货数量：若命中差异且值为 0，改从表格别名解析
    if quantity is not None:
        # 粗检：前文紧邻「差异」则作废，交给表头解析
        m_bad = re.search(
            r"差异数量\s*[:：]?\s*" + re.escape(quantity),
            ocr_text or "",
        )
        if m_bad:
            quantity = None
    unit_price = first(
        r"(?:未税单价|单价|Unit\s*Price)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    if total_amount:
        total_amount = total_amount.replace(",", "")
    if net_amount:
        net_amount = net_amount.replace(",", "")
    if tax_amount:
        tax_amount = tax_amount.replace(",", "")
    if quantity:
        quantity = quantity.replace(",", "")
    if unit_price:
        unit_price = unit_price.replace(",", "")
    supplier = first(
        r"(?:销售方名称|供应商名称|供应商|Supplier)\s*[:：]?\s*([^\r\n]+)"
        r"|乙\s*方\s*[（(]?(?:卖方|供方|销售方)?[）)]?\s*[:：]\s*([^登记统一社会信用\r\n]{4,48})"
    )
    buyer = first(
        r"(?:购买方名称|购方名称|购买方|买方名称|买方|Buyer)\s*[:：]?\s*([^\r\n]+)"
        r"|甲\s*方\s*[（(]?(?:买方|需方|采购方)?[）)]?\s*[:：]\s*([^登记统一社会信用\r\n]{4,48})"
    )
    order_no = first(
        r"(?:业务编号|订单号|采购订单号|销售订单)\s*[:：]?\s*([A-Z0-9][A-Z0-9_-]{3,})"
    )
    payment = first(
        r"((?:增值税)?发票开具之日起\s*\d+\s*[日天]内[^\r\n。；]{0,40})"
        r"|(开具之日起\s*\d+\s*[日天]内[^\r\n。；]{0,40})"
        r"|(买方应在[^\r\n。；]{0,30}\d+\s*[日天][^\r\n。；]{0,40}支付[^\r\n。；]{0,30})"
        r"|(签收后\s*\d+\s*[日天][^\r\n。；]{0,24})"
        r"|(验收后\s*\d+\s*[日天][^\r\n。；]{0,24})"
        r"|(票到\s*\d+\s*[日天][^\r\n。；]{0,24})"
        r"|(开票后\s*\d+\s*[日天][^\r\n。；]{0,24})"
        r"|(电汇[^\r\n。；]{0,40}支付[^\r\n。；]{0,20})"
    )
    receipt_date = first(
        r"(?:签收日期|入库日期|验收日期|收货日期|交货日期)\s*[:：]?\s*"
        r"(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    )
    posting_date = first(
        r"(?:入账日期|记账日期|过账日期|Posting\s*Date)\s*[:：]?\s*"
        r"(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    )
    contract_no = first(
        r"(?:合同编号|合同号|合同索引号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,})"
    )

    def _norm_date(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        parts = re.match(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})", raw)
        if not parts:
            return None
        return f"{parts.group(1)}-{int(parts.group(2)):02d}-{int(parts.group(3)):02d}"

    fields: Dict[str, Any] = {}
    if document_no:
        fields["documentNo"] = document_no
        fields["invoiceNo"] = document_no
    if raw_date:
        normalized = _norm_date(raw_date)
        if normalized:
            fields["documentDate"] = normalized
    if net_amount:
        fields["amount"] = net_amount
    if tax_amount:
        fields["taxAmount"] = tax_amount
    if tax_rate:
        fields["taxRate"] = tax_rate
    if discount:
        fields["discountRate"] = discount.replace(" ", "")
    if discount_amt:
        fields["discountAmount"] = discount_amt.replace(",", "")
    if quantity:
        fields["quantity"] = quantity
    if unit_price:
        fields["unitPrice"] = unit_price
    if total_amount:
        fields["totalAmount"] = total_amount
    if supplier:
        fields["supplierName"] = supplier.strip()
    if buyer:
        fields["buyerName"] = buyer.strip()
    if order_no:
        fields["orderNo"] = order_no
        fields.setdefault("documentNo", order_no)
    if payment:
        fields["paymentTerms"] = payment
        fields.setdefault("settlementTerms", payment)

    # 合同条款启发式（正文中有则写入）
    transport = first(
        r"(?:运输(?:方式|条款)|交货地点|运费承担|贸易术语)\s*[:：]?\s*([^\r\n]{4,80})"
        r"|((?:FOB|CIF|CFR|CIP|DDP|EXW)\s+[^\r\n]{0,40})"
        r"|((?:运费由(?:卖方|买方|甲方|乙方)承担)[^\r\n]{0,40})"
        r"|(运输／贸易条款为[^\r\n。；]{4,60})"
    )
    if transport:
        fields["transportTerms"] = transport.strip()
    compact = re.sub(r"\s+", "", ocr_text or "")

    def first_compact(pattern: str) -> Optional[str]:
        m = re.search(pattern, compact, flags=re.I)
        if not m:
            return None
        if m.lastindex:
            for i in range(1, m.lastindex + 1):
                g = m.group(i)
                if g and str(g).strip():
                    return str(g).strip()
        return m.group(0).strip() if m.group(0) else None

    control = first_compact(
        r"(以验收期[^。；;]{8,100}控制权转移的日期)"
        r"|(验收期[^。；;]{0,80}完成控制权转移)"
        r"|((?:验收合格后|签收后|交付后)[^。；;]{0,40}(?:控制权|所有权|风险)[^。；;]{0,40})"
    )
    if not control:
        control = first(
            r"((?:货物)?控制权转移[^。\r\n；;]{0,30})"
            r"|((?:验收期|到货签收)[^。\r\n；;]{0,60}控制权转移[^。\r\n；;]{0,40})"
        )
        if control and len(control) < 12:
            control = None
    if control:
        fields["controlTransferTerms"] = control.strip()
    # 签收/验收日语义留给 apply_receipt_date_fields（按单据类型门控），
    # 避免合同/订单正文提到「到货」就被写成截止用签收日。
    rd = _norm_date(receipt_date)
    if rd:
        fields["deliveryDate"] = rd
        fields.setdefault("documentDate", rd)
    pd = _norm_date(posting_date)
    if pd:
        fields["postingDate"] = pd
    if contract_no:
        fields["contractNo"] = contract_no
    # 金额归一化（落库为元）；无有效金额时不填默认值
    fields, _ = normalize_extracted_fields(fields, ocr_text)
    return fields


def extract_table_anchored_fields(ocr_text: str, document_type: str) -> Dict[str, Any]:
    """从已识别的表格行提取可复算事实。

    OCR/LLM 都可能把 ``MAT-05777`` 中的尾数当成数量，或把税额/若干行金额
    拼成价税合计。对已命中单据表头和同一明细行的值，以表格位置关系作为更强
    的证据来源；本函数只输出能直接从文字层复核的字段，不为缺失字段编造 0。
    """
    text = str(ocr_text or "")
    kind = _normalize_doc_type(document_type)
    result: Dict[str, Any] = {}

    if kind == "invoice":
        gross = re.search(
            r"价\s*税\s*合\s*计[^¥￥\d]{0,80}[（(]?\s*小写[）)]?\s*[¥￥]\s*([\d,]+(?:\.\d{1,2})?)",
            text,
            flags=re.I | re.S,
        )
        if gross:
            result["totalAmount"] = gross.group(1).replace(",", "")

        line = re.search(
            r"(?:MAT|SKU)[-\s]?[A-Z0-9-]+\s+(?:件|个|台|套|PCS?)\s+(\d+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d{2})?)\s+(\d+(?:\.\d+)?)%\s+([\d,]+(?:\.\d{2})?)",
            text,
            flags=re.I,
        )
        if line:
            result.update(
                {
                    "quantity": line.group(1).replace(",", ""),
                    "unitPrice": line.group(2).replace(",", ""),
                    "amount": line.group(3).replace(",", ""),
                    "taxRate": line.group(4),
                    "taxAmount": line.group(5).replace(",", ""),
                }
            )

    if kind == "warehouse_receipt":
        line = re.search(
            r"(?:MAT|SKU)[-\s]?[A-Z0-9-]+.*?(?:件|个|台|套|PCS?)\s+(\d+(?:\.\d+)?)",
            text,
            flags=re.I,
        )
        if line:
            result["quantity"] = line.group(1).replace(",", "")

        buyer = re.search(
            r"验收单位信息\s*[:：]?\s*(?:\n|.){0,180}?公司\s*[:：]\s*([^\s：:；;，,]+(?:有限公司|股份有限公司|有限责任公司|公司))",
            text,
            flags=re.S,
        )
        if buyer:
            result["buyerName"] = buyer.group(1).strip()

    return result


class LegacyOcrAdapter:
    """调用千帆 PaddleOCR + Chat Completions，输出结构化单据字段。"""

    def __init__(
        self,
        *,
        qianfan_api_key: Optional[str] = None,
        qianfan_secret_key: Optional[str] = None,
        qianfan_access_key: Optional[str] = None,
        llm_api_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        ocr_model: Optional[str] = None,
        use_mock_when_unavailable: Optional[bool] = None,
    ) -> None:
        # 每次实例化时重新加载 .env，避免 Streamlit 长驻进程读到旧配置
        load_dotenv(settings.BASE_DIR / ".env", override=True)

        self.qianfan_api_key = (
            qianfan_api_key
            or os.getenv("QIANFAN_API_KEY")
            or settings.QIANFAN_API_KEY
            or ""
        ).strip()
        self.qianfan_secret_key = (
            qianfan_secret_key
            or os.getenv("QIANFAN_SECRET_KEY")
            or settings.QIANFAN_SECRET_KEY
            or ""
        ).strip()
        self.qianfan_access_key = (
            qianfan_access_key
            or os.getenv("QIANFAN_ACCESS_KEY")
            or settings.QIANFAN_ACCESS_KEY
            or ""
        ).strip()
        self.llm_api_url = llm_api_url or settings.LLM_API_URL
        self.llm_api_key = (
            llm_api_key
            or os.getenv("LLM_API_KEY")
            or settings.LLM_API_KEY
            or self.qianfan_api_key
        ).strip()
        self.llm_model = llm_model or settings.LLM_MODEL
        self.ocr_model = ocr_model or settings.QIANFAN_OCR_MODEL
        if use_mock_when_unavailable is None:
            allow = str(
                os.getenv("AUDIT_ALLOW_OCR_MOCK")
                or getattr(settings, "AUDIT_ALLOW_OCR_MOCK", "0")
                or "0"
            ).strip().lower()
            self.use_mock_when_unavailable = allow in {"1", "true", "yes", "on"}
        else:
            self.use_mock_when_unavailable = bool(use_mock_when_unavailable)
        self._oauth_token_cache: dict[str, tuple[str, float]] = {}

        if self.is_api_configured():
            auth_mode = (
                "bce-v3 API Key"
                if self.qianfan_api_key.startswith("bce-v3/")
                else "AK/SK access_token"
            )
            msg = f"[OK] 百度千帆API已就绪（鉴权方式：{auth_mode}）"
            logger.info(msg)
        else:
            if self.use_mock_when_unavailable:
                msg = "[WARN] API Key未配置，将使用Mock模式"
            else:
                msg = "[WARN] API Key未配置，且已禁止 OCR Mock（AUDIT_ALLOW_OCR_MOCK=0）"
            logger.warning(msg)

    def is_api_configured(self) -> bool:
        if is_valid_api_credential(self.qianfan_api_key) and self.qianfan_api_key.startswith(
            "bce-v3/"
        ):
            return True
        ak = self.qianfan_access_key or self.qianfan_api_key
        sk = self.qianfan_secret_key
        return is_valid_api_credential(ak) and is_valid_api_credential(sk)

    def _fetch_oauth_access_token(self, api_key: str, secret_key: str) -> str:
        cache_key = f"{api_key}:{secret_key}"
        cached = self._oauth_token_cache.get(cache_key)
        now = time.time()
        if cached and cached[1] > now:
            return cached[0]

        logger.info("正在使用 AK/SK 获取百度 access_token…")
        resp = requests.post(
            OAUTH_TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": secret_key,
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error(
                "获取 access_token 失败 status={} body={}",
                resp.status_code,
                resp.text[:2000],
            )
            resp.raise_for_status()
        data = resp.json()
        token = str(data.get("access_token") or "")
        if not token:
            raise RuntimeError(f"OAuth 响应缺少 access_token: {data}")
        expires_in = int(data.get("expires_in", 2592000))
        self._oauth_token_cache[cache_key] = (token, now + max(expires_in - 60, 60))
        logger.info("access_token 获取成功，有效期约 {} 秒", expires_in)
        return token

    def _get_authorization_header(self) -> str:
        if is_valid_api_credential(self.qianfan_api_key) and self.qianfan_api_key.startswith(
            "bce-v3/"
        ):
            return f"Bearer {self.qianfan_api_key}"

        api_key = self.qianfan_access_key or self.qianfan_api_key
        secret_key = self.qianfan_secret_key
        if is_valid_api_credential(api_key) and is_valid_api_credential(secret_key):
            token = self._fetch_oauth_access_token(api_key, secret_key)
            return f"Bearer {token}"

        raise RuntimeError(
            "QIANFAN_API_KEY 未配置或仍为占位符，无法调用 PaddleOCR。"
            "请在 .env 中填入 bce-v3/ALTAK-... 或 QIANFAN_ACCESS_KEY + QIANFAN_SECRET_KEY。"
        )

    def _read_file_as_base64(self, file_path: str) -> tuple[str, int]:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        file_type = 0 if path.suffix.lower() == ".pdf" else 1
        return b64, file_type

    def _resolve_ocr_input_path(self, file_path: str) -> tuple[str, dict[str, Any]]:
        """图片在 OCR 前走 L1 预处理；返回实际读图路径与元数据。"""
        path = Path(file_path)
        if path.suffix.lower() == ".pdf":
            return file_path, {}
        if "_ocr_work" in path.parts:
            return file_path, {}
        try:
            from src.image_preprocess import prepare_for_ocr, preprocess_enabled

            if not preprocess_enabled():
                return file_path, {}
            result = prepare_for_ocr(path, cache_dir=path.parent / "_ocr_work")
            meta = dict(result.meta or {})
            meta["profile"] = result.profile
            meta["applied"] = result.applied
            if result.applied:
                meta["ocr_image_path"] = str(result.ocr_path)
            return str(result.ocr_path), meta
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR 预处理失败，直通原图 path={} err={}", file_path, exc)
            return file_path, {"profile": "error", "error": str(exc)}

    def _call_paddle_ocr(self, file_b64_or_url: str, file_type: Optional[int]) -> Dict[str, Any]:
        auth_header = self._get_authorization_header()

        is_url = file_b64_or_url.startswith("http://") or file_b64_or_url.startswith(
            "https://"
        )
        body: Dict[str, Any] = {
            "model": self.ocr_model,
            "file": file_b64_or_url,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
        }
        if not is_url and file_type is not None:
            body["fileType"] = file_type

        logger.info(
            "调用 PaddleOCR model={} fileType={} payload_bytes≈{}",
            self.ocr_model,
            file_type,
            len(file_b64_or_url),
        )
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = requests.post(
                    PADDLE_OCR_URL,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": auth_header,
                    },
                    timeout=settings.QIANFAN_OCR_TIMEOUT_MS / 1000.0,
                )
                if resp.status_code >= 400:
                    logger.error(
                        "PaddleOCR HTTP 错误 status={} body={}",
                        resp.status_code,
                        resp.text[:3000],
                    )
                    resp.raise_for_status()
                payload = resp.json()
                if payload.get("error") or payload.get("error_code"):
                    logger.error(
                        "PaddleOCR 业务错误: {}",
                        json.dumps(payload, ensure_ascii=False)[:3000],
                    )
                    raise RuntimeError(f"PaddleOCR 返回错误: {payload}")
                return payload
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    logger.warning("PaddleOCR 第 1 次失败，1 秒后重试: {}", exc)
                    time.sleep(1)
        assert last_exc is not None
        raise last_exc

    def _parse_paddle_response(self, payload: Dict[str, Any]) -> tuple[str, float, list]:
        layout_results = (
            payload.get("result", {}).get("layoutParsingResults")
            or payload.get("result", {}).get("layout_parsing_results")
            or []
        )
        parts: list[str] = []
        for page in layout_results:
            md = (page.get("markdown") or {}).get("text")
            if md:
                parts.append(str(md))
                continue
            pruned = page.get("prunedResult") or page.get("pruned_result") or {}
            for block in pruned.get("parsing_res_list") or []:
                content = block.get("block_content")
                if content:
                    parts.append(str(content))
        raw = "\n\n".join(parts).strip()
        if not raw:
            # 某些返回把文字放在 result.ocrResults
            ocr_results = payload.get("result", {}).get("ocrResults") or []
            texts = []
            for item in ocr_results:
                for line in item.get("words_result") or item.get("wordsResult") or []:
                    w = line.get("words") or line.get("text")
                    if w:
                        texts.append(str(w))
            raw = "\n".join(texts)
        from src.legacy_ocr.text_blocks import extract_text_blocks_from_paddle

        blocks = extract_text_blocks_from_paddle(payload)
        return raw, 0.97 if raw else 0.0, blocks

    def recognize_document(
        self,
        file_path: str,
        document_type: str,
        *,
        allow_degraded: bool = True,
    ) -> dict:
        """返回含 rawText 的结构化结果。

        优先走 PDF 内嵌文字层（跳过远程 OCR）；不足再调千帆；失败仍回退文字层/Mock。
        """
        doc_type = _normalize_doc_type(document_type)
        # 文本型 PDF：本地抽取即可，避免 120s 级远程 OCR
        pdf_text = _extract_pdf_text_layer(file_path)
        if pdf_text and len(pdf_text.strip()) >= 40:
            logger.info(
                "PDF 文字层短路 path={} chars={}",
                file_path,
                len(pdf_text),
            )
            return OcrResult(
                rawText=pdf_text,
                extractedFields={},
                confidence=0.92,
                documentType=doc_type,
                source="pdf_text",
            ).model_dump()

        try:
            ocr_input, preprocess_meta = self._resolve_ocr_input_path(file_path)
            b64, file_type = self._read_file_as_base64(ocr_input)
            payload = self._call_paddle_ocr(b64, file_type)
            raw_text, confidence, text_blocks = self._parse_paddle_response(payload)
            if not raw_text:
                raise RuntimeError("PaddleOCR 未返回可解析文本")
            result = OcrResult(
                rawText=raw_text,
                extractedFields={},
                confidence=confidence,
                documentType=doc_type,
                source="paddleocr",
                textBlocks=text_blocks,
            )
            out = result.model_dump()
            if preprocess_meta:
                out["preprocess"] = preprocess_meta
                ocr_img = preprocess_meta.get("ocr_image_path")
                if ocr_img:
                    out["ocr_image_path"] = ocr_img
            return out
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PaddleOCR 失败 path={} err={} type={}",
                file_path,
                exc,
                type(exc).__name__,
            )
            if pdf_text and len(pdf_text.strip()) >= 20:
                logger.warning(
                    "PaddleOCR 失败，回退 PDF 文字层 path={} chars={}",
                    file_path,
                    len(pdf_text),
                )
                return OcrResult(
                    rawText=pdf_text,
                    extractedFields={},
                    confidence=0.85,
                    documentType=doc_type,
                    source="pdf_text",
                ).model_dump()
            if allow_degraded and not self.use_mock_when_unavailable:
                return OcrResult(
                    rawText="",
                    extractedFields={},
                    confidence=0.0,
                    documentType=doc_type,
                    source="ocr_failed",
                ).model_dump()
            if not self.use_mock_when_unavailable:
                raise
            logger.warning("PaddleOCR 降级 Mock 模式 path={}", file_path)
            result = OcrResult(
                rawText=mock_raw_text(doc_type),
                extractedFields=mock_fields(doc_type),
                confidence=0.5,
                documentType=doc_type,
                source="mock",
            )
            return result.model_dump()

    def _llm_chat_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 1200,
    ) -> Dict[str, Any]:
        """调用千帆 Chat，返回解析后的 JSON 对象。"""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            self.llm_api_url,
            json={
                "model": self.llm_model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "max_tokens": max_tokens,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": self._get_authorization_header()
                if is_valid_api_credential(self.llm_api_key)
                and self.llm_api_key.startswith("bce-v3/")
                else f"Bearer {self.llm_api_key}",
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = (
            resp.json().get("choices", [{}])[0].get("message", {}).get("content")
            or "{}"
        )
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return _parse_llm_json(str(content))

    def _build_extract_prompt(
        self,
        doc_type: str,
        ocr_raw_text: str,
        base: Dict[str, Any],
        *,
        only_fields: Optional[List[str]] = None,
    ) -> str:
        from src.llm.prompts import build_field_gap_fill_user

        type_name = _doc_type_label(doc_type)
        unresolved = list(only_fields or [])
        if not unresolved:
            # 全量抽取：把关键语义字段都列为待补，模型可覆盖/保留
            unresolved = [
                "documentNo",
                "documentDate",
                "amount",
                "taxAmount",
                "totalAmount",
                "quantity",
                "unit",
                "supplierName",
                "supplierTaxId",
                "buyerName",
                "buyerTaxId",
                "paymentTerms",
                "settlementTerms",
                "transportTerms",
                "controlTransferTerms",
                "performanceObligations",
                "deliveryDate",
                "acceptanceDate",
                "discountRate",
                "discountAmount",
                "taxRate",
                "warehouseNo",
                "projectName",
                "remarks",
                "invoiceCode",
                "invoiceNo",
                "contractNo",
                "orderNo",
                "items",
            ]
        return build_field_gap_fill_user(
            doc_type_label=type_name,
            ocr_text=ocr_raw_text,
            unresolved_fields=unresolved,
            rule_fields=base,
            semantic_hint=_semantic_instructions(doc_type),
        )

    def extract_fields(
        self,
        ocr_raw_text: str,
        document_type: str = "other",
        *,
        target_fields: Optional[List[str]] = None,
        fast_batch: bool = False,
    ) -> dict:
        """从 OCR 文本提取字段。

        默认 llm_first：语义理解为主，正则仅作种子/兜底，避免每遇新表述就加规则。
        target_fields：OCR 前确认的字段清单；有值时 LLM 优先抽这些键（含自定义）。
        """
        doc_type = _normalize_doc_type(document_type)
        base = extract_fields_heuristically(ocr_raw_text)
        anchored = extract_table_anchored_fields(ocr_raw_text, doc_type)
        # 表头+同一行的事实可直接复核，优先级高于自由文本的正则猜测。
        base.update(anchored)
        mode = (
            os.getenv("FIELD_EXTRACT_MODE")
            or getattr(settings, "FIELD_EXTRACT_MODE", None)
            or "llm_first"
        ).strip().lower()
        planned = [str(x).strip() for x in (target_fields or []) if str(x).strip()]

        def _finalize(fields: Dict[str, Any]) -> Dict[str, Any]:
            normalized, _ = normalize_extracted_fields(fields, ocr_raw_text)
            return apply_receipt_date_fields(
                normalized, ocr_raw_text, document_type=doc_type
            )

        if mode == "heuristic" or not is_valid_api_credential(self.llm_api_key):
            if mode != "heuristic":
                logger.warning("LLM Key 未配置，使用启发式提取")
            fields = _finalize(base)
            if not fields.get("totalAmount") and not self.use_mock_when_unavailable:
                fields["_totalAmountMissing"] = True
            return fields

        # smart / 批处理加速：启发式够用时跳过 LLM（含合同 PDF 文字层场景）
        if (mode == "smart" or fast_batch) and not planned:
            missing_critical = not (
                base.get("supplierName")
                and base.get("documentDate")
                and base.get("documentNo")
            )
            has_amount = base.get("totalAmount") is not None and not base.get(
                "_totalAmountMissing"
            )
            force_llm = doc_type in {"warehouse_receipt", "contract"} and not fast_batch
            if (
                not force_llm
                and not missing_critical
                and has_amount
                and len(base) >= 4
            ):
                return _finalize(base)
            if fast_batch and has_amount and len(base) >= 3:
                return _finalize(base)

        # llm_first（默认）与 smart 需 LLM 的路径
        try:
            from src.llm.prompts import UNIFIED_SYSTEM_PROMPT

            ai_fields = self._llm_chat_json(
                self._build_extract_prompt(
                    doc_type,
                    ocr_raw_text,
                    base,
                    only_fields=planned or None,
                ),
                system=UNIFIED_SYSTEM_PROMPT,
            )
            merged = _merge_ai_and_base(ai_fields, base)
            # 关键语义字段仍缺 → 二次补抽（针对缺失字段提问，不扩正则）
            still_missing = _missing_semantic_fields(doc_type, merged)
            if planned:
                still_missing = [
                    k
                    for k in planned
                    if k not in {"documentType", "items"}
                    and not str(merged.get(k) or "").strip()
                ]
            if still_missing and ocr_raw_text.strip():
                logger.info(
                    "LLM gap-fill doc={} missing={}", doc_type, still_missing
                )
                try:
                    gap = self._llm_chat_json(
                        self._build_extract_prompt(
                            doc_type,
                            ocr_raw_text,
                            merged,
                            only_fields=still_missing,
                        ),
                        system=UNIFIED_SYSTEM_PROMPT,
                        max_tokens=600,
                    )
                    for k in still_missing:
                        if gap.get(k) and not merged.get(k):
                            merged[k] = gap[k]
                except Exception as gap_exc:  # noqa: BLE001
                    logger.warning("LLM gap-fill failed: {}", gap_exc)
            return _finalize(merged)
        except Exception as exc:  # noqa: BLE001
            err_body = ""
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                err_body = exc.response.text[:2000]
            logger.error("LLM extract failed: {} body={}", exc, err_body)
            fields = _finalize(base)
            if not fields and self.use_mock_when_unavailable:
                return mock_fields(doc_type)
            return fields

    def gap_fill_missing_fields(
        self,
        ocr_raw_text: str,
        document_type: str,
        current_fields: Dict[str, Any],
        *,
        only_fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """仅针对缺失字段再跑一轮 LLM；无 Key / 无正文则原样返回。"""
        doc_type = _normalize_doc_type(document_type)
        merged = dict(current_fields or {})
        missing = list(only_fields or _missing_semantic_fields(doc_type, merged))
        if not missing or not str(ocr_raw_text or "").strip():
            return merged
        if not is_valid_api_credential(self.llm_api_key):
            logger.warning("LLM Key 未配置，跳过字段 gap-fill")
            return merged
        try:
            from src.llm.prompts import UNIFIED_SYSTEM_PROMPT

            # 截断超长正文，避免补抽一次等满 60s
            text = str(ocr_raw_text or "")
            if len(text) > 6000:
                text = text[:6000] + "\n…(正文已截断)"
            gap = self._llm_chat_json(
                self._build_extract_prompt(
                    doc_type,
                    text,
                    merged,
                    only_fields=missing,
                ),
                system=UNIFIED_SYSTEM_PROMPT,
                max_tokens=500,
            )
            for k in missing:
                val = gap.get(k) if isinstance(gap, dict) else None
                if val in (None, "", "null", "None"):
                    continue
                if not _field_present(merged, k):
                    merged[k] = val
            normalized, _ = normalize_extracted_fields(merged, ocr_raw_text)
            return apply_receipt_date_fields(
                normalized, ocr_raw_text, document_type=doc_type
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("gap_fill_missing_fields failed: {}", exc)
            return merged

    def recognize_and_extract(
        self,
        file_path: str,
        document_type: str,
        *,
        allow_degraded: bool = True,
    ) -> dict:
        """OCR + 字段提取一步到位。"""
        doc_type = _normalize_doc_type(document_type)
        ocr = self.recognize_document(file_path, doc_type, allow_degraded=allow_degraded)
        raw_text = ocr.get("rawText") or ""
        source = str(ocr.get("source") or "unknown")
        if source == "mock" and ocr.get("extractedFields"):
            fields = dict(ocr["extractedFields"])
        elif source == "ocr_failed":
            fields = {}
        else:
            fields = self.extract_fields(raw_text, doc_type)
        fields, _ = normalize_extracted_fields(fields, raw_text)
        fields = apply_receipt_date_fields(fields, raw_text, document_type=doc_type)
        extracted = dict(fields)
        extracted["documentType"] = doc_type
        if source == "ocr_failed":
            extracted["_ocrFailed"] = True
        result = OcrResult(
            rawText=raw_text,
            extractedFields=extracted,
            confidence=float(ocr.get("confidence") or 0.0),
            documentType=doc_type,
            source=source,
        )
        return result.model_dump()

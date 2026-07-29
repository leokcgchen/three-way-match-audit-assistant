"""Legacy OCR 适配器：千帆 PaddleOCR + LLM 字段提取（精简 Python 实现）。"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

from config.settings import is_valid_api_credential, settings
from src.legacy_ocr.field_normalize import normalize_extracted_fields
from src.legacy_ocr.mock_data import mock_fields, mock_raw_text
from src.legacy_ocr.models import OcrResult
from src.utils.date_extractor import apply_receipt_date_fields, resolve_receipt_dates
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
        return m.group(1).strip() if m else None

    document_no = first(
        r"(?:发票号码|发票号|采购订单号|订单号|入库单号|Invoice\s*(?:No|Number)|PO\s*(?:No|Number))\s*[:：]?\s*([A-Z0-9][A-Z0-9_-]{3,})"
    )
    raw_date = first(
        r"(?:开票日期|发票日期|订单日期|单据日期|日期|Invoice\s*Date)\s*[:：]?\s*(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    )
    total_amount = first(
        r"(?:价税合计|含税总金额|合计金额|总金额|金额合计|Total\s*Amount|Grand\s*Total)"
        r"\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    net_amount = first(
        r"(?:未税金额|不含税金额|未税小计|金额)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    tax_amount = first(
        r"(?:税额|Tax\s*Amount)\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{1,2})?)"
    )
    tax_rate = first(r"税率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%?")
    quantity = first(
        r"(?:数量|Quantity)\s*[:：]?\s*([\d,]+(?:\.\d+)?)"
    )
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
    supplier = first(r"(?:销售方名称|供应商名称|供应商|Supplier)\s*[:：]?\s*([^\r\n]+)")
    payment = first(
        r"(?:付款条款|账期|Payment\s*Terms?)\s*[:：]?\s*([^\r\n]+)"
        r"|(签收后\s*\d+\s*[日天]|验收后\s*\d+\s*[日天]|票到\s*\d+\s*[日天])"
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
    if quantity:
        fields["quantity"] = quantity
    if unit_price:
        fields["unitPrice"] = unit_price
    if total_amount:
        fields["totalAmount"] = total_amount
    if supplier:
        fields["supplierName"] = supplier
    if payment:
        fields["paymentTerms"] = payment
    if re.search(r"验收|签收|入库|到货", ocr_text, flags=re.I):
        resolved = resolve_receipt_dates(ocr_text, payment_terms=payment)
        for key in ("deliveryDate", "acceptanceDate", "receiptDateForCutoff", "_receiptDateSource"):
            val = resolved.get(key)
            if val:
                fields[key] = val
        cutoff = resolved.get("receiptDateForCutoff")
        if cutoff:
            fields["documentDate"] = cutoff
        elif receipt_date:
            rd = _norm_date(receipt_date)
            if rd:
                fields["deliveryDate"] = rd
                fields.setdefault("documentDate", rd)
    else:
        rd = _norm_date(receipt_date)
        if rd:
            fields["deliveryDate"] = rd
            fields.setdefault("documentDate", rd)
    pd = _norm_date(posting_date)
    if pd:
        fields["postingDate"] = pd
    if contract_no:
        fields["contractNo"] = contract_no
    # 金额归一化（万元）；无有效金额时不填默认值
    fields, _ = normalize_extracted_fields(fields, ocr_text)
    return fields


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
        use_mock_when_unavailable: bool = True,
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
        self.use_mock_when_unavailable = use_mock_when_unavailable
        self._oauth_token_cache: dict[str, tuple[str, float]] = {}

        if self.is_api_configured():
            auth_mode = (
                "bce-v3 API Key"
                if self.qianfan_api_key.startswith("bce-v3/")
                else "AK/SK access_token"
            )
            msg = f"✅ 百度千帆API已就绪（鉴权方式：{auth_mode}）"
            print(msg)
            logger.info(msg)
        else:
            msg = "⚠️ API Key未配置，将使用Mock模式"
            print(msg)
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

    def _parse_paddle_response(self, payload: Dict[str, Any]) -> tuple[str, float]:
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
        return raw, 0.97 if raw else 0.0

    def recognize_document(
        self,
        file_path: str,
        document_type: str,
        *,
        allow_degraded: bool = True,
    ) -> dict:
        """调用 PaddleOCR，返回含 rawText 的结构化结果。"""
        doc_type = _normalize_doc_type(document_type)
        try:
            b64, file_type = self._read_file_as_base64(file_path)
            payload = self._call_paddle_ocr(b64, file_type)
            raw_text, confidence = self._parse_paddle_response(payload)
            if not raw_text:
                raise RuntimeError("PaddleOCR 未返回可解析文本")
            result = OcrResult(
                rawText=raw_text,
                extractedFields={},
                confidence=confidence,
                documentType=doc_type,
                source="paddleocr",
            )
            return result.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PaddleOCR 失败 path={} err={} type={}",
                file_path,
                exc,
                type(exc).__name__,
            )
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

    def extract_fields(self, ocr_raw_text: str, document_type: str = "other") -> dict:
        """从 OCR 文本提取字段（LLM，失败则启发式）。"""
        doc_type = _normalize_doc_type(document_type)
        base = extract_fields_heuristically(ocr_raw_text)
        missing_critical = not (
            base.get("supplierName")
            and base.get("documentDate")
            and base.get("documentNo")
        )
        has_amount = base.get("totalAmount") is not None and not base.get(
            "_totalAmountMissing"
        )
        # 签收单/合同需语义理解，不因启发式“够用”而跳过 LLM
        force_llm = doc_type in {"warehouse_receipt", "contract"}

        if (
            not force_llm
            and not missing_critical
            and has_amount
            and len(base) >= 4
        ):
            fields, _ = normalize_extracted_fields(base, ocr_raw_text)
            return apply_receipt_date_fields(fields, ocr_raw_text, document_type=doc_type)

        if not is_valid_api_credential(self.llm_api_key):
            logger.warning("LLM_API_KEY/QIANFAN_API_KEY 未配置，使用启发式提取")
            fields, _ = normalize_extracted_fields(base, ocr_raw_text)
            if not fields.get("totalAmount") and not self.use_mock_when_unavailable:
                fields["_totalAmountMissing"] = True
            return apply_receipt_date_fields(fields, ocr_raw_text, document_type=doc_type)

        type_name = {
            "purchase_order": "采购订单",
            "warehouse_receipt": "入库单/验收单",
            "invoice": "增值税发票",
            "contract": "采购/销售合同",
        }.get(doc_type, doc_type)

        already = (
            f"\n\n已识别字段（请保留并补充缺失）：\n{json.dumps(base, ensure_ascii=False)}"
            if base
            else ""
        )
        receipt_semantics = ""
        if doc_type == "warehouse_receipt":
            receipt_semantics = """
【签收/验收日期规则（审计截止性测试）】
- deliveryDate：实物到货日（若文中有「到货日/实物到货」）
- acceptanceDate：验收完成日、期限届满日或验收合格日（截止性测试优先使用）
- 若同时出现到货日与验收完成日，两个字段分别填写，不可互相替代
- 若仅有到货日且正文/合同写明 N 日验收期，acceptanceDate = 到货日 + N 天
- documentDate 填 acceptanceDate（若无则填 deliveryDate）
"""
        elif doc_type == "contract":
            receipt_semantics = """
【合同账期】
- paymentTerms 提取完整付款/验收条款（如「签收后10日」「3日验收期」）
"""

        prompt = f"""你是一位专业的财务审计助手。请从以下【{type_name}】OCR文本提取关键字段，只返回JSON。{already}
{receipt_semantics}
OCR文本：
---
{ocr_raw_text[:8000]}
---

字段：documentNo, documentDate(YYYY-MM-DD), amount, taxAmount, totalAmount, quantity, unit,
supplierName, supplierTaxId, buyerName, buyerTaxId, paymentTerms, deliveryDate, acceptanceDate,
warehouseNo, projectName, remarks, invoiceCode, invoiceNo, contractNo,
items:[{{name,quantity,unit,unitPrice,amount}}]
金额只保留数字；数量禁止空格拆字；缺失字段用 null。"""

        try:
            resp = requests.post(
                self.llm_api_url,
                json={
                    "model": self.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 800,
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
            ai_fields = _parse_llm_json(str(content))
            preserve_ai = {"acceptanceDate", "receiptDateForCutoff", "deliveryDate", "paymentTerms"}
            merged = dict(ai_fields)
            for k, v in base.items():
                if not v:
                    continue
                if k in preserve_ai and merged.get(k):
                    continue
                merged[k] = v
            fields, _ = normalize_extracted_fields(merged, ocr_raw_text)
            return apply_receipt_date_fields(fields, ocr_raw_text, document_type=doc_type)
        except Exception as exc:  # noqa: BLE001
            err_body = ""
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                err_body = exc.response.text[:2000]
            logger.error("LLM extract failed: {} body={}", exc, err_body)
            fields, _ = normalize_extracted_fields(base, ocr_raw_text)
            if not fields and self.use_mock_when_unavailable:
                return mock_fields(doc_type)
            return apply_receipt_date_fields(fields, ocr_raw_text, document_type=doc_type)

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

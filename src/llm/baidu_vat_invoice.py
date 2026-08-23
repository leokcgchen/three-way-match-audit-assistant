# -*- coding: utf-8 -*-
"""Baidu VAT invoice OCR specialist (advisory structured fields).

Uses classic Baidu OCR ``vat_invoice`` API with AK/SK OAuth token.
Never writes accepted_value — callers map results into ambiguity candidates only.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, Optional

import requests

from config.settings import is_valid_api_credential, settings

_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def _ak_sk() -> tuple[str, str]:
    ak = (
        os.getenv("BAIDU_OCR_API_KEY")
        or os.getenv("BAIDU_OCR_ACCESS_KEY")
        or getattr(settings, "BAIDU_OCR_API_KEY", "")
        or settings.QIANFAN_ACCESS_KEY
        or settings.QIANFAN_API_KEY
        or ""
    ).strip()
    sk = (
        os.getenv("BAIDU_OCR_SECRET_KEY")
        or getattr(settings, "BAIDU_OCR_SECRET_KEY", "")
        or settings.QIANFAN_SECRET_KEY
        or ""
    ).strip()
    # If AK looks like bce-v3 bearer, not usable for classic OCR OAuth
    if ak.startswith("bce-v3/"):
        ak = (
            os.getenv("BAIDU_OCR_API_KEY")
            or getattr(settings, "BAIDU_OCR_API_KEY", "")
            or settings.QIANFAN_ACCESS_KEY
            or ""
        ).strip()
    return ak, sk


def vat_invoice_status() -> Dict[str, Any]:
    ak, sk = _ak_sk()
    ok = is_valid_api_credential(ak) and is_valid_api_credential(sk) and not ak.startswith("bce-v3/")
    return {
        "enabled": str(getattr(settings, "AMOUNT_AMBIGUITY_AUTO_VAT", "1") or "1").strip().lower()
        not in {"0", "false", "off", "no"},
        "configured": ok,
        "api": "vat_invoice",
        "endpoint": "https://aip.baidubce.com/rest/2.0/ocr/v1/vat_invoice",
    }


def _access_token() -> str:
    status = vat_invoice_status()
    if not status["configured"]:
        raise ValueError("Baidu VAT invoice OCR AK/SK is not configured")
    now = time.time()
    cached = str(_TOKEN_CACHE.get("access_token") or "")
    if cached and float(_TOKEN_CACHE.get("expires_at") or 0) > now + 60:
        return cached
    ak, sk = _ak_sk()
    resp = requests.post(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={
            "grant_type": "client_credentials",
            "client_id": ak,
            "client_secret": sk,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = str(data.get("access_token") or "").strip()
    if not token:
        err = data.get("error_description") or data.get("error") or data
        raise ValueError(f"Baidu OAuth failed: {err}")
    expires_in = int(data.get("expires_in") or 2592000)
    _TOKEN_CACHE["access_token"] = token
    _TOKEN_CACHE["expires_at"] = now + expires_in
    return token


def _parse_amount(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("¥", "").replace("￥", "").replace(" ", "")
    if not text:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def extract_vat_invoice_amounts(image_bytes: bytes) -> Dict[str, Any]:
    """Call Baidu vat_invoice and return amount triad + raw words.

    Returns
    -------
    dict with keys:
      ok, provider, amount, taxAmount, totalAmount, words_result, error
    """
    status = vat_invoice_status()
    if not status["enabled"]:
        raise ValueError("VAT invoice specialist is disabled")
    if not image_bytes:
        raise ValueError("invoice image is empty")

    token = _access_token()
    resp = requests.post(
        status["endpoint"],
        params={"access_token": token},
        data={"image": base64.b64encode(image_bytes).decode("ascii")},
        timeout=60,
    )
    # Baidu often returns 200 with error_code in body
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"VAT invoice response not JSON: {exc}") from exc
    if resp.status_code >= 400:
        raise ValueError(f"VAT invoice HTTP {resp.status_code}: {payload}")
    err_code = payload.get("error_code")
    if err_code not in (None, 0, "0"):
        return {
            "ok": False,
            "provider": "baidu_vat_invoice",
            "error_code": err_code,
            "error": str(payload.get("error_msg") or payload),
            "amount": None,
            "taxAmount": None,
            "totalAmount": None,
            "words_result": payload.get("words_result"),
        }

    words = payload.get("words_result") or {}
    if not isinstance(words, dict):
        words = {}
    amount = _parse_amount(words.get("TotalAmount"))
    tax = _parse_amount(words.get("TotalTax"))
    total = _parse_amount(words.get("AmountInFiguers") or words.get("AmountInFigures"))
    return {
        "ok": amount is not None or tax is not None or total is not None,
        "provider": "baidu_vat_invoice",
        "error_code": None,
        "error": None,
        "amount": amount,
        "taxAmount": tax,
        "totalAmount": total,
        "words_result": words,
    }

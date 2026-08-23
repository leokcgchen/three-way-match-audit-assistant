"""正式口径下 REQUIRE_* 自动开启。"""

from __future__ import annotations

import src.api.hitl_gate as hitl


def test_require_auto_on_when_formal(monkeypatch):
    monkeypatch.setenv("AUDIT_ALLOW_OCR_MOCK", "0")
    monkeypatch.delenv("REQUIRE_FIELDS_CONFIRMED_API", raising=False)
    monkeypatch.setattr(hitl.settings, "REQUIRE_FIELDS_CONFIRMED_API", "auto")
    monkeypatch.setattr(hitl.settings, "AUDIT_ALLOW_OCR_MOCK", "0")
    assert hitl.formal_ocr_mode() is True
    assert hitl.fields_confirmed_api_required() is True


def test_require_auto_off_when_mock_allowed(monkeypatch):
    monkeypatch.setenv("AUDIT_ALLOW_OCR_MOCK", "1")
    monkeypatch.delenv("REQUIRE_FIELDS_CONFIRMED_API", raising=False)
    monkeypatch.setattr(hitl.settings, "REQUIRE_FIELDS_CONFIRMED_API", "auto")
    monkeypatch.setattr(hitl.settings, "AUDIT_ALLOW_OCR_MOCK", "1")
    assert hitl.formal_ocr_mode() is False
    assert hitl.fields_confirmed_api_required() is False


def test_require_explicit_zero_overrides_formal(monkeypatch):
    monkeypatch.setenv("AUDIT_ALLOW_OCR_MOCK", "0")
    monkeypatch.setenv("REQUIRE_FIELDS_CONFIRMED_API", "0")
    monkeypatch.setattr(hitl.settings, "AUDIT_ALLOW_OCR_MOCK", "0")
    assert hitl.fields_confirmed_api_required() is False


def test_require_explicit_one_when_mock(monkeypatch):
    monkeypatch.setenv("AUDIT_ALLOW_OCR_MOCK", "1")
    monkeypatch.setenv("REQUIRE_MATCHING_CONFIRMED_API", "1")
    assert hitl.matching_confirmed_api_required() is True

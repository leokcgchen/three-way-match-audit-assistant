"""OCR 前 L1 预处理与预览路径对齐。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest


cv2 = pytest.importorskip("cv2")


def _write_gray_jpg(path: Path, gray: int) -> None:
    img = np.full((400, 300, 3), gray, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path.write_bytes(buf.tobytes())


def test_passthrough_white_page(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_IMAGE_PREPROCESS", "1")
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "white.jpg"
    _write_gray_jpg(src, 250)
    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")
    assert out.profile == "passthrough"
    assert out.ocr_path == src
    assert out.applied is False


def test_l1_geometry_on_dark_page(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_IMAGE_PREPROCESS", "1")
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "handheld.jpg"
    _write_gray_jpg(src, 120)
    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")
    assert out.profile == "l1_geometry"
    assert out.applied is True
    assert out.ocr_path.suffix.lower() == ".jpg"
    assert out.ocr_path.is_file()
    assert out.ocr_path != src


def test_resolve_preview_uses_ocr_image_path(tmp_path: Path):
    from src.image_preprocess.preview_path import resolve_document_image_path

    original = tmp_path / "scan.pdf"
    original.write_bytes(b"%PDF-1.4")
    ocr_img = tmp_path / "_ocr_work" / "scan_l1.jpg"
    ocr_img.parent.mkdir(parents=True)
    _write_gray_jpg(ocr_img, 180)
    doc = {"path": str(original), "ocr_image_path": str(ocr_img)}
    assert resolve_document_image_path(doc) == ocr_img


def test_preprocess_disabled(tmp_path: Path, monkeypatch):
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "0")
    src = tmp_path / "dark.jpg"
    _write_gray_jpg(src, 100)
    out = svc.prepare_for_ocr(src)
    assert out.profile == "disabled"
    assert out.ocr_path == src

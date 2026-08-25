"""OCR 前 L1 预处理与预览路径对齐。"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import pytest


cv2 = pytest.importorskip("cv2")


def _write_gray_jpg(path: Path, gray: int) -> None:
    img = np.full((400, 300, 3), gray, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path.write_bytes(buf.tobytes())


def _write_gray_document(path: Path, *, width: int = 1200, height: int = 1600) -> None:
    img = np.full((height, width, 3), 145, dtype=np.uint8)
    for y in range(90, height - 60, 70):
        cv2.putText(img, f"AUDIT 2026-08-24 AMOUNT 1,234.56 ROW {y}", (55, y), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (25, 25, 25), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    path.write_bytes(buf.tobytes())


def _read_gray(path: Path) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def _write_shadow_document(path: Path, *, width: int = 900, height: int = 1200) -> None:
    gradient = np.tile(np.linspace(75, 205, width, dtype=np.uint8), (height, 1))
    img = cv2.merge((gradient, gradient, gradient))
    for y in range(80, height - 40, 65):
        cv2.putText(img, f"INVOICE AMOUNT 1,234.56 ROW {y}", (35, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
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


def test_gray_document_is_enhanced_before_ocr(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_IMAGE_PREPROCESS", "1")
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "gray-document.jpg"
    _write_gray_document(src)

    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")

    assert out.profile == "l1_enhanced"
    assert out.applied is True
    assert out.meta["enhancement_applied"] is True
    assert out.meta["enhancement_route"] in {"balanced", "shadow", "low_contrast", "blurred"}
    assert "enhance" in out.meta["steps"]
    original = _read_gray(src)
    enhanced = _read_gray(out.ocr_path)
    assert enhanced is not None and original is not None
    assert float(np.median(enhanced)) > float(np.median(original)) + 15


def test_shadow_document_uses_shadow_enhancement(tmp_path: Path, monkeypatch):
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "shadow-document.jpg"
    _write_shadow_document(src)

    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")

    assert out.meta["enhancement_route"] == "shadow"
    assert out.meta["enhancement_recipe"] == "fast_shadow_v0"
    assert out.meta["enhancement_applied"] is True
    assert out.meta["enhancement_status"] == "applied"


def test_enhancement_error_falls_back_without_blocking_ocr(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_IMAGE_PREPROCESS", "1")
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    monkeypatch.setattr(
        svc,
        "apply_color_scan",
        lambda _pixels: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )
    monkeypatch.setattr(
        svc,
        "apply_fast_recipe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )
    src = tmp_path / "gray-fallback.jpg"
    _write_gray_document(src)

    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")

    assert out.applied is True
    assert out.ocr_path.is_file()
    assert out.meta["enhancement_applied"] is False
    assert out.meta["enhancement_status"] == "fallback_error"


def test_2_4mp_preprocess_finishes_within_three_seconds(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_IMAGE_PREPROCESS", "1")
    from src.image_preprocess import service as svc

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "gray-2_4mp.jpg"
    _write_gray_document(src, width=1600, height=1500)

    started = perf_counter()
    out = svc.prepare_for_ocr(src, cache_dir=tmp_path / "cache")
    elapsed = perf_counter() - started

    assert out.meta["enhancement_applied"] is True
    assert elapsed < 3.0, f"single-page preprocessing took {elapsed:.3f}s"


def test_ocr_adapter_resolves_to_the_enhanced_image(tmp_path: Path, monkeypatch):
    from src.image_preprocess import service as svc
    from src.legacy_ocr import LegacyOcrAdapter

    monkeypatch.setattr(svc.settings, "AUDIT_IMAGE_PREPROCESS", "1")
    src = tmp_path / "gray-ocr-input.jpg"
    _write_gray_document(src)

    adapter = LegacyOcrAdapter(use_mock_when_unavailable=True)
    actual_path, meta = adapter._resolve_ocr_input_path(str(src))

    assert Path(actual_path).is_file()
    assert Path(actual_path) != src
    assert meta["profile"] == "l1_enhanced"
    assert meta["enhancement_applied"] is True
    assert meta["ocr_image_path"] == actual_path


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

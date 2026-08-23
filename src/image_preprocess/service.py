"""L1 几何预处理：白页直通；手持/灰页做方向（可选 Paddle）+ deskew + warp。"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from config.settings import settings
from src.utils.logger import logger

from .color_scan import confident_document_warp, evaluate_photo_enhance_trigger
from .fast_image import (
    WorkingImage,
    decode_working_image,
    encode_delivery_jpeg,
    rotate_with_white_canvas,
)
from .quality import estimate_deskew

_ORIENTATION_LOCK = threading.RLock()
_ORIENTATION_RUNNER: Any | None = None
_ORIENTATION_RUNNER_FAILED = False

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"})


@dataclass(frozen=True)
class PreprocessResult:
    source_path: Path
    ocr_path: Path
    profile: str
    applied: bool
    meta: dict[str, Any] = field(default_factory=dict)


def preprocess_enabled() -> bool:
    flag = str(getattr(settings, "AUDIT_IMAGE_PREPROCESS", "1") or "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _is_raster_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file()


def _cache_path(source: Path, cache_dir: Path) -> Path:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    return cache_dir / f"{source.stem}_{digest}_l1.jpg"


def _orientation_runner():
    global _ORIENTATION_RUNNER, _ORIENTATION_RUNNER_FAILED
    if _ORIENTATION_RUNNER_FAILED:
        return None
    if _ORIENTATION_RUNNER is not None:
        return _ORIENTATION_RUNNER
    worker_py = str(getattr(settings, "AUDIT_ORIENTATION_WORKER_PYTHON", "") or "").strip()
    model_root = str(getattr(settings, "AUDIT_ORIENTATION_MODEL_ROOT", "") or "").strip()
    if not worker_py or not model_root:
        return None
    worker_path = Path(worker_py)
    model_path = Path(model_root)
    if not worker_path.is_file() or not model_path.is_dir():
        _ORIENTATION_RUNNER_FAILED = True
        logger.warning(
            "方向模型未配置或路径无效 worker={} model_root={}，跳过 Paddle 方向纠正",
            worker_py,
            model_root,
        )
        return None
    runtime = Path(
        str(getattr(settings, "AUDIT_IMAGE_PREPROCESS_RUNTIME", "") or "D:/AuditImageLabRuntime/preprocess-runtime")
    )
    try:
        from .orientation_runner import OrientationWorkerRunner

        runner = OrientationWorkerRunner(
            worker_path,
            data_root=runtime,
            model_root=model_path,
            timeout_seconds=float(getattr(settings, "AUDIT_ORIENTATION_TIMEOUT_SECONDS", 8) or 8),
        )
        runner.start()
        _ORIENTATION_RUNNER = runner
        return runner
    except Exception as exc:  # noqa: BLE001
        _ORIENTATION_RUNNER_FAILED = True
        logger.warning("方向 worker 启动失败，跳过 Paddle 方向：{}", exc)
        return None


def _predict_orientation(source_path: Path) -> tuple[int, dict[str, Any]]:
    with _ORIENTATION_LOCK:
        runner = _orientation_runner()
    if runner is None:
        return 0, {"orientation_status": "skipped", "reason": "no_worker"}
    try:
        with _ORIENTATION_LOCK:
            result = runner.predict(source_path)
        if getattr(result, "verified", False) or getattr(result, "status", "") == "MANUAL_OVERRIDE":
            deg = int(getattr(result, "correction_degrees", 0) or 0)
            return deg, {
                "orientation_status": str(getattr(result, "status", "")),
                "orientation_degrees": deg,
                "orientation_confidence": getattr(result, "confidence", None),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("方向预测失败 path={} err={}", source_path, exc)
    return 0, {"orientation_status": "failed"}


def _apply_l1_geometry(pixels: np.ndarray, source_path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    meta: dict[str, Any] = {"steps": []}
    orientation_deg, ometa = _predict_orientation(source_path)
    meta.update(ometa)
    out = pixels
    if orientation_deg:
        out = rotate_with_white_canvas(out, -orientation_deg)
        meta["steps"].append("orientation")
        meta["orientation_applied_degrees"] = orientation_deg

    deskew_deg, deskew_conf = estimate_deskew(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    applied_deskew = 0.0
    if abs(deskew_deg) >= 0.5 and deskew_conf >= 0.1:
        applied_deskew = float(deskew_deg)
        out = rotate_with_white_canvas(out, applied_deskew)
        meta["steps"].append("deskew")
    meta["deskew_applied_degrees"] = applied_deskew
    meta["deskew_confidence"] = round(float(deskew_conf), 3)

    out, warped = confident_document_warp(out)
    meta["warp_applied"] = bool(warped)
    if warped:
        meta["steps"].append("warp")
    return out, meta


def prepare_for_ocr(
    source_path: str | Path,
    *,
    cache_dir: Path | None = None,
) -> PreprocessResult:
    """返回 OCR 应读取的路径；预处理图与原件分离，预览/高亮走 ocr_image_path。"""
    src = Path(source_path).resolve()
    if not preprocess_enabled() or not _is_raster_image(src):
        return PreprocessResult(
            source_path=src,
            ocr_path=src,
            profile="disabled" if not preprocess_enabled() else "not_image",
            applied=False,
            meta={},
        )

    try:
        working: WorkingImage = decode_working_image(src, 2.4, 4.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("预处理解码失败，直通 OCR path={} err={}", src, exc)
        return PreprocessResult(
            source_path=src,
            ocr_path=src,
            profile="decode_failed",
            applied=False,
            meta={"error": str(exc)},
        )

    trigger = evaluate_photo_enhance_trigger(working.pixels)
    if not trigger.needed:
        jpeg_bytes, quality = encode_delivery_jpeg(working.pixels)
        del jpeg_bytes
        return PreprocessResult(
            source_path=src,
            ocr_path=src,
            profile="passthrough",
            applied=False,
            meta={
                "median_luminance": round(trigger.median_luminance, 1),
                "trigger_reason": trigger.reason,
                "jpeg_quality": quality,
            },
        )

    pixels, geom_meta = _apply_l1_geometry(working.pixels, src)
    jpeg_bytes, quality = encode_delivery_jpeg(pixels)
    out_dir = cache_dir or (src.parent / "_ocr_work")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _cache_path(src, out_dir)
    out_path.write_bytes(jpeg_bytes)

    meta = {
        "median_luminance": round(trigger.median_luminance, 1),
        "trigger_reason": trigger.reason,
        "jpeg_quality": quality,
        **geom_meta,
    }
    logger.info(
        "L1 预处理完成 src={} out={} steps={}",
        src.name,
        out_path.name,
        meta.get("steps"),
    )
    return PreprocessResult(
        source_path=src,
        ocr_path=out_path,
        profile="l1_geometry",
        applied=True,
        meta=meta,
    )

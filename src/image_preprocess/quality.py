from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy import ndimage
from skimage import color, filters, transform


@dataclass(frozen=True)
class SourceAnalysis:
    deskew_degrees: float
    deskew_confidence: float
    quarter_turn_degrees: int
    quarter_turn_confidence: float
    orientation_review_required: bool
    orientation_note: str
    detected_issues: tuple[str, ...]


@dataclass(frozen=True)
class QualityMetrics:
    sharpness: float
    local_contrast: float
    background_uniformity: float
    noise_risk: float
    clipping_risk: float
    readability_proxy: float
    residual_skew_degrees: float
    delta_readability: float = 0.0
    delta_sharpness: float = 0.0
    delta_local_contrast: float = 0.0


def _gray(pixels: np.ndarray) -> np.ndarray:
    normalized = np.asarray(pixels, dtype=np.float32)
    if normalized.max(initial=0) > 1.0:
        normalized /= 255.0
    if normalized.ndim == 3:
        normalized = color.rgb2gray(normalized[..., :3])
    return np.clip(normalized, 0, 1)


def _analysis_gray(pixels: np.ndarray, max_side: int = 900) -> np.ndarray:
    gray = _gray(pixels)
    largest = max(gray.shape)
    if largest <= max_side:
        return gray
    scale = max_side / largest
    return transform.resize(
        gray,
        (max(2, round(gray.shape[0] * scale)), max(2, round(gray.shape[1] * scale))),
        anti_aliasing=True,
    )


def _projection_score(gray: np.ndarray, correction_degrees: float) -> float:
    rotated = transform.rotate(gray, correction_degrees, resize=True, mode="constant", cval=1.0, preserve_range=True)
    if float(np.ptp(rotated)) < 0.015:
        return 0.0
    threshold = filters.threshold_otsu(rotated)
    ink = rotated < min(0.92, threshold)
    projection = ink.sum(axis=1).astype(np.float64)
    if projection.size < 3 or projection.max(initial=0) == 0:
        return 0.0
    return float(np.mean(np.diff(projection) ** 2) / (projection.mean() + 1.0))


def estimate_deskew(pixels: np.ndarray) -> tuple[float, float]:
    gray = _analysis_gray(pixels)
    if float(np.ptp(gray)) < 0.015:
        return 0.0, 0.0
    coarse_angles = np.arange(-12.0, 12.01, 1.0)
    coarse_scores = np.asarray([_projection_score(gray, angle) for angle in coarse_angles])
    best_coarse = float(coarse_angles[int(np.argmax(coarse_scores))])
    fine_angles = np.arange(max(-12.0, best_coarse - 1.0), min(12.0, best_coarse + 1.0) + 0.01, 0.25)
    fine_scores = np.asarray([_projection_score(gray, angle) for angle in fine_angles])
    best_index = int(np.argmax(fine_scores))
    best_angle = float(fine_angles[best_index])
    baseline = max(_projection_score(gray, 0.0), 1e-9)
    confidence = float(np.clip((fine_scores[best_index] - baseline) / baseline, 0, 1))
    if abs(best_angle) < 0.5:
        best_angle = 0.0
    return best_angle, confidence


def _quarter_turn_analysis(pixels: np.ndarray) -> tuple[int, float]:
    gray = _analysis_gray(pixels)
    score_0 = _projection_score(gray, 0)
    score_90 = _projection_score(np.rot90(gray, k=-1), 0)
    strongest = max(score_0, score_90, 1e-9)
    confidence = abs(score_0 - score_90) / strongest
    if score_90 > score_0 and confidence >= 0.18:
        # Projection profiles distinguish portrait from landscape, but the
        # 90°/270° direction is symmetric without OCR. Return one candidate
        # direction and require the caller/UI to keep it human-confirmable.
        return 90, float(min(confidence, 1.0))
    return 0, float(min(confidence, 1.0))


def analyze_pixels(pixels: np.ndarray) -> SourceAnalysis:
    quarter_turn, quarter_confidence = _quarter_turn_analysis(pixels)
    oriented = np.rot90(pixels, k=-(quarter_turn // 90)) if quarter_turn else pixels
    deskew_degrees, deskew_confidence = estimate_deskew(oriented)
    metrics = quality_metrics(oriented, residual_skew=deskew_degrees, estimate_skew=False)
    issues: list[str] = []
    if quarter_turn:
        issues.append("检测到页面横竖方向异常，需通过 Paddle 本地模型或人工确认方向")
    if abs(deskew_degrees) >= 0.5:
        issues.append(f"检测到约 {abs(deskew_degrees):.1f}° 倾斜")
    if metrics.background_uniformity < 72:
        issues.append("背景亮度不均或存在阴影")
    if metrics.local_contrast < 28:
        issues.append("局部对比度偏低")
    if metrics.noise_risk > 45:
        issues.append("存在明显颗粒或噪声风险")
    if metrics.sharpness < 18:
        issues.append("字迹与线条边缘偏软")
    if not issues:
        issues.append("未发现明显的几何或画质异常")
    return SourceAnalysis(
        deskew_degrees=round(deskew_degrees, 2),
        deskew_confidence=round(deskew_confidence, 3),
        quarter_turn_degrees=quarter_turn,
        quarter_turn_confidence=round(quarter_confidence, 3),
        orientation_review_required=True,
        orientation_note="像素算法无法可靠区分顺时针 90°、270° 或 180°，需人工确认。",
        detected_issues=tuple(issues),
    )


def quality_metrics(
    pixels: np.ndarray, *, residual_skew: float | None = None, estimate_skew: bool = True
) -> QualityMetrics:
    gray = _analysis_gray(pixels, max_side=1200)
    laplacian = ndimage.laplace(gray)
    sharpness_raw = float(np.var(laplacian))
    sharpness = float(np.clip(100 * (1 - np.exp(-sharpness_raw * 120)), 0, 100))

    local_mean = ndimage.uniform_filter(gray, size=25)
    local_contrast_raw = float(np.mean(np.abs(gray - local_mean)))
    local_contrast = float(np.clip(local_contrast_raw * 650, 0, 100))

    bright = gray[gray >= np.quantile(gray, 0.65)]
    background_std = float(np.std(bright)) if bright.size else 0.0
    background_uniformity = float(np.clip(100 - background_std * 500, 0, 100))

    smooth = ndimage.median_filter(gray, size=3)
    noise_raw = float(np.median(np.abs(gray - smooth)))
    noise_risk = float(np.clip(noise_raw * 1600, 0, 100))

    dark_clip = float(np.mean(gray <= 0.01))
    # White paper is expected to occupy most of a document image; counting
    # saturated white background as clipping makes clean PDFs look maximally
    # damaged. Dark saturation remains a useful proxy for crushed text strokes,
    # while washed-out text is already reflected by local contrast/readability.
    clipping_risk = float(np.clip(dark_clip * 250, 0, 100))

    readability = (
        0.34 * sharpness
        + 0.30 * local_contrast
        + 0.22 * background_uniformity
        + 0.14 * (100 - noise_risk)
        - 0.22 * clipping_risk
    )
    if residual_skew is None and estimate_skew:
        residual_skew = estimate_deskew(pixels)[0]
    residual_skew = float(residual_skew or 0.0)
    readability -= min(abs(residual_skew) * 1.8, 18)
    return QualityMetrics(
        sharpness=round(sharpness, 2),
        local_contrast=round(local_contrast, 2),
        background_uniformity=round(background_uniformity, 2),
        noise_risk=round(noise_risk, 2),
        clipping_risk=round(clipping_risk, 2),
        readability_proxy=round(float(np.clip(readability, 0, 100)), 2),
        residual_skew_degrees=round(residual_skew, 2),
    )


def compare_quality(source: QualityMetrics, candidate: QualityMetrics) -> QualityMetrics:
    return replace(
        candidate,
        delta_readability=round(candidate.readability_proxy - source.readability_proxy, 2),
        delta_sharpness=round(candidate.sharpness - source.sharpness, 2),
        delta_local_contrast=round(candidate.local_contrast - source.local_contrast, 2),
    )

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from .fast_image import _sharpen_text_edges, _validate_pixels

DocumentWarper = Callable[[np.ndarray], tuple[np.ndarray, bool]]
ColorScanApplier = Callable[[np.ndarray], np.ndarray]


def _order_corners(points: np.ndarray) -> np.ndarray:
    ordered = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = ordered.sum(axis=1)
    diffs = np.diff(ordered, axis=1).reshape(4)
    top_left = ordered[int(np.argmin(sums))]
    bottom_right = ordered[int(np.argmax(sums))]
    top_right = ordered[int(np.argmin(diffs))]
    bottom_left = ordered[int(np.argmax(diffs))]
    return np.stack((top_left, top_right, bottom_right, bottom_left)).astype(np.float32)


def _corner_angles(points: np.ndarray) -> tuple[float, ...]:
    ordered = _order_corners(points)
    angles: list[float] = []
    for index in range(4):
        previous = ordered[(index - 1) % 4]
        current = ordered[index]
        following = ordered[(index + 1) % 4]
        incoming = previous - current
        outgoing = following - current
        incoming_norm = float(np.linalg.norm(incoming))
        outgoing_norm = float(np.linalg.norm(outgoing))
        if incoming_norm < 1 or outgoing_norm < 1:
            return ()
        cosine = float(np.clip(np.dot(incoming, outgoing) / (incoming_norm * outgoing_norm), -1.0, 1.0))
        angles.append(math.degrees(math.acos(cosine)))
    return tuple(angles)


def _edge_direction_delta(first: np.ndarray, second: np.ndarray) -> float:
    first_angle = math.degrees(math.atan2(float(first[1]), float(first[0])))
    second_angle = math.degrees(math.atan2(float(second[1]), float(second[0])))
    delta = abs(first_angle - second_angle) % 180.0
    return min(delta, 180.0 - delta)


def _quad_is_plausible(quad: np.ndarray, width: int, height: int) -> bool:
    ordered = _order_corners(quad)
    margin = 12.0
    on_border = 0
    for x, y in ordered:
        if x <= margin or y <= margin or x >= width - 1 - margin or y >= height - 1 - margin:
            on_border += 1
    if on_border >= 3:
        return False
    width_top = float(np.linalg.norm(ordered[1] - ordered[0]))
    width_bottom = float(np.linalg.norm(ordered[2] - ordered[3]))
    height_left = float(np.linalg.norm(ordered[3] - ordered[0]))
    height_right = float(np.linalg.norm(ordered[2] - ordered[1]))
    width_ratio = min(width_top, width_bottom) / max(max(width_top, width_bottom), 1.0)
    height_ratio = min(height_left, height_right) / max(max(height_left, height_right), 1.0)
    if width_ratio < 0.80 or height_ratio < 0.80:
        return False
    top = ordered[1] - ordered[0]
    bottom = ordered[2] - ordered[3]
    left = ordered[3] - ordered[0]
    right = ordered[2] - ordered[1]
    if _edge_direction_delta(top, bottom) > 15.0:
        return False
    if _edge_direction_delta(left, right) > 15.0:
        return False
    return True


def detect_document_quad(pixels: np.ndarray) -> np.ndarray | None:
    _validate_pixels(pixels)
    height, width = pixels.shape[:2]
    scale = min(1.0, 512.0 / max(height, width))
    if scale < 1.0:
        small = cv2.resize(
            pixels,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        small = pixels
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark_mean = float(np.mean(gray[binary == 0])) if np.any(binary == 0) else 0.0
    light_mean = float(np.mean(gray[binary == 255])) if np.any(binary == 255) else 255.0
    if light_mean < dark_mean:
        binary = cv2.bitwise_not(binary)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8), iterations=1)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    frame_area = float(small.shape[0] * small.shape[1])
    best: np.ndarray | None = None
    best_area = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = float(cv2.contourArea(contour))
        peri = cv2.arcLength(contour, True)
        candidate = None
        for epsilon in (0.015, 0.02, 0.03, 0.04):
            approx = cv2.approxPolyDP(contour, epsilon * max(peri, 1.0), True)
            if len(approx) == 4:
                candidate = approx.reshape(4, 2).astype(np.float32)
                break
        if candidate is None:
            box = cv2.boxPoints(cv2.minAreaRect(contour))
            box_area = float(cv2.contourArea(box))
            if box_area <= 0 or abs(box_area - area) / max(box_area, 1.0) > 0.28:
                continue
            candidate = box.astype(np.float32)
        candidate_area = float(cv2.contourArea(candidate))
        ratio = candidate_area / frame_area
        if ratio < 0.38 or ratio > 0.93:
            continue
        if not cv2.isContourConvex(np.round(candidate).astype(np.int32)):
            continue
        angles = _corner_angles(candidate)
        if len(angles) != 4 or min(angles) < 52 or max(angles) > 128:
            continue
        original = (candidate / scale).astype(np.float32)
        if not _quad_is_plausible(original, width, height):
            continue
        if candidate_area <= best_area:
            continue
        best = original
        best_area = candidate_area
    if best is None:
        return None
    return best


def warp_document(pixels: np.ndarray, quad: np.ndarray) -> np.ndarray:
    _validate_pixels(pixels)
    ordered = _order_corners(quad)
    width_top = float(np.linalg.norm(ordered[1] - ordered[0]))
    width_bottom = float(np.linalg.norm(ordered[2] - ordered[3]))
    height_left = float(np.linalg.norm(ordered[3] - ordered[0]))
    height_right = float(np.linalg.norm(ordered[2] - ordered[1]))
    output_width = max(32, int(round(max(width_top, width_bottom))))
    output_height = max(32, int(round(max(height_left, height_right))))
    destination = np.array(
        [
            [0, 0],
            [output_width - 1, 0],
            [output_width - 1, output_height - 1],
            [0, output_height - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    warped = cv2.warpPerspective(
        pixels,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return np.ascontiguousarray(warped, dtype=np.uint8)


def confident_document_warp(pixels: np.ndarray) -> tuple[np.ndarray, bool]:
    quad = detect_document_quad(pixels)
    if quad is None:
        return pixels, False
    height, width = pixels.shape[:2]
    if not _quad_is_plausible(quad, width, height):
        return pixels, False
    warped = warp_document(pixels, quad)
    source_area = float(pixels.shape[0] * pixels.shape[1])
    warped_area = float(warped.shape[0] * warped.shape[1])
    if warped_area < 0.2 * source_area or warped_area > 1.8 * source_area:
        return pixels, False
    return warped, True


ALREADY_SHARP_LAPLACIAN = 80.0
BRIGHT_PAPER_MEDIAN = 200.0
ILLUMINATION_SPREAD = 25.0
SYSTEM_EXPORT_GRAY_MEDIAN = 221.0
TRIGGER_THUMBNAIL = 128


@dataclass(frozen=True)
class PhotoEnhanceTrigger:
    needed: bool
    median_luminance: float
    reason: str


def _trigger_gray_thumbnail(pixels: np.ndarray) -> np.ndarray:
    height, width = pixels.shape[:2]
    scale = min(1.0, TRIGGER_THUMBNAIL / max(height, width))
    if scale < 1.0:
        sample = cv2.resize(
            pixels,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        sample = pixels
    return cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)


def evaluate_photo_enhance_trigger(pixels: np.ndarray) -> PhotoEnhanceTrigger:
    _validate_pixels(pixels)
    median = float(np.median(_trigger_gray_thumbnail(pixels)))
    if median >= SYSTEM_EXPORT_GRAY_MEDIAN:
        return PhotoEnhanceTrigger(False, median, "system_export_white")
    return PhotoEnhanceTrigger(True, median, "handheld_photo_gray")


def needs_photo_enhance(pixels: np.ndarray) -> bool:
    return evaluate_photo_enhance_trigger(pixels).needed


def _luminance_is_already_sharp(luminance: np.ndarray) -> bool:
    variance = float(cv2.Laplacian(luminance, cv2.CV_64F).var())
    return variance >= ALREADY_SHARP_LAPLACIAN


def _needs_illumination_flatten(luminance: np.ndarray) -> bool:
    if float(np.median(luminance)) < BRIGHT_PAPER_MEDIAN:
        return True
    height, width = luminance.shape[:2]
    block = max(8, min(height, width) // 4)
    tile_h = (height // block) * block
    tile_w = (width // block) * block
    if tile_h < block or tile_w < block:
        return True
    paper_tiles = luminance[:tile_h, :tile_w].reshape(
        tile_h // block,
        block,
        tile_w // block,
        block,
    ).max(axis=(1, 3))
    low, high = np.percentile(paper_tiles.astype(np.float32), (10, 90))
    return float(high - low) >= ILLUMINATION_SPREAD


def apply_color_scan(pixels: np.ndarray) -> np.ndarray:
    _validate_pixels(pixels)
    lab = cv2.cvtColor(pixels, cv2.COLOR_BGR2LAB)
    luminance, channel_a, channel_b = cv2.split(lab)
    if _needs_illumination_flatten(luminance):
        kernel_size = max(21, min(41, (min(luminance.shape[:2]) // 14) | 1))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        paper = cv2.dilate(luminance, kernel)
        paper = cv2.GaussianBlur(paper, (0, 0), sigmaX=7, sigmaY=7).astype(np.float32)
        normalized = np.clip(
            luminance.astype(np.float32) / np.maximum(paper, 12.0) * 255.0,
            0,
            255,
        )
    else:
        normalized = luminance.astype(np.float32)
    low, high = np.percentile(normalized, (3, 82))
    stretched = np.clip((normalized - low) * (255.0 / max(high - low, 8.0)), 0, 255)
    paper_lift = np.clip((stretched - 150.0) * 0.55, 0, 70)
    stretched = np.clip(stretched + paper_lift, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(16, 16))
    stretched = clahe.apply(stretched)
    paper_mask = stretched >= 208
    channel_a = channel_a.astype(np.float32)
    channel_b = channel_b.astype(np.float32)
    channel_a[paper_mask] = 128.0 + (channel_a[paper_mask] - 128.0) * 0.12
    channel_b[paper_mask] = 128.0 + (channel_b[paper_mask] - 128.0) * 0.12
    merged = cv2.merge(
        (
            stretched,
            np.clip(channel_a, 0, 255).astype(np.uint8),
            np.clip(channel_b, 0, 255).astype(np.uint8),
        )
    )
    enhanced = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    if _luminance_is_already_sharp(stretched):
        return enhanced
    return _sharpen_text_edges(enhanced, 0.82)

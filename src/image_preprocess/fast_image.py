from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from .route_contract import ROUTE_NAMES, RouteName

OcrEqualityCheck = Callable[[np.ndarray, int], bool]


RECIPE_VARIANTS: dict[RouteName, tuple[tuple[float, float, int], ...]] = {
    "balanced": ((1.8, 0.72, 0),),
    "shadow": ((2.0, 0.58, 3), (2.4, 0.78, 5), (1.8, 0.48, 3)),
    "low_contrast": ((2.4, 0.68, 0), (3.0, 0.88, 0)),
    "blurred": ((2.0, 0.88, 3), (2.2, 1.05, 3), (1.8, 0.72, 5)),
    "line_art": ((2.0, 0.55, 1),),
}


@dataclass(frozen=True)
class WorkingImage:
    pixels: np.ndarray
    width: int
    height: int
    decode_scale: float
    source_sha256: str


@dataclass(frozen=True)
class DefectRoute:
    name: RouteName
    difficult: bool
    candidate_count: int

    def __post_init__(self) -> None:
        if self.name not in ROUTE_NAMES:
            raise ValueError(f"Unsupported defect route: {self.name!r}")
        if not isinstance(self.difficult, bool):
            raise ValueError("difficult must be a boolean")
        expected_count = len(RECIPE_VARIANTS[self.name])
        if self.candidate_count != expected_count or not 1 <= self.candidate_count <= 3:
            raise ValueError(
                f"candidate_count for {self.name!r} must equal its bounded recipe count ({expected_count})"
            )


@dataclass(frozen=True)
class ThumbnailStatistics:
    percentile_contrast: float
    illumination_spread: float
    laplacian_variance: float
    saturation_median: float
    edge_density: float


@dataclass(frozen=True)
class EncodingQualityFloor:
    max_edge_energy_loss: float = 0.03
    ocr_equality_check: OcrEqualityCheck | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_edge_energy_loss, bool)
            or not isinstance(self.max_edge_energy_loss, (int, float))
            or not math.isfinite(self.max_edge_energy_loss)
            or not 0 <= self.max_edge_energy_loss <= 1
        ):
            raise ValueError("max_edge_energy_loss must be a finite number from 0 to 1")
        if self.ocr_equality_check is not None and not callable(self.ocr_equality_check):
            raise ValueError("ocr_equality_check must be callable")


@dataclass(frozen=True)
class EncodedImage:
    destination: Path
    quality: int
    size_bytes: int
    sha256: str
    sampled_edge_energy_loss: float
    edge_floor_met: bool
    ocr_equality_passed: bool | None


_JPEG_REDUCED_FLAGS: tuple[tuple[int, float, int], ...] = (
    (8, 0.125, cv2.IMREAD_REDUCED_COLOR_8),
    (4, 0.25, cv2.IMREAD_REDUCED_COLOR_4),
    (2, 0.5, cv2.IMREAD_REDUCED_COLOR_2),
    (1, 1.0, cv2.IMREAD_COLOR),
)


def _positive_megapixels(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


def _validate_pixels(pixels: np.ndarray) -> None:
    if not isinstance(pixels, np.ndarray):
        raise TypeError("pixels must be a NumPy array")
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("pixels must be a non-empty uint8 BGR image with shape (height, width, 3)")
    if pixels.shape[0] == 0 or pixels.shape[1] == 0:
        raise ValueError("pixels must be a non-empty uint8 BGR image with shape (height, width, 3)")


def target_dimensions(width: int, height: int, target_megapixels: float) -> tuple[int, int]:
    if isinstance(width, bool) or isinstance(height, bool) or width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers")
    target = _positive_megapixels(target_megapixels, "target_megapixels")
    pixels = width * height
    target_pixels = int(target * 1_000_000)
    if target_pixels < 1:
        raise ValueError("target_megapixels must allow at least one output pixel")
    if pixels <= target_pixels:
        return width, height
    scale = math.sqrt(target_pixels / pixels)
    output_width = max(1, round(width * scale))
    output_height = max(1, round(height * scale))
    if output_width * output_height > target_pixels:
        if output_width >= output_height:
            output_width = max(1, target_pixels // output_height)
        else:
            output_height = max(1, target_pixels // output_width)
    return output_width, output_height


def _jpeg_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise ValueError("JPEG signature did not match JPEG metadata")
            return image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Unable to read JPEG metadata: {path}") from exc


def _reduced_jpeg_flag(width: int, height: int, target_megapixels: float) -> tuple[int, float]:
    target_pixels = target_megapixels * 1_000_000
    for divisor, scale, flag in _JPEG_REDUCED_FLAGS:
        decoded_width = math.ceil(width / divisor)
        decoded_height = math.ceil(height / divisor)
        if decoded_width * decoded_height >= target_pixels or divisor == 1:
            return flag, scale
    raise AssertionError("unreachable JPEG reduction selection")


def decode_working_image(path: Path, target_megapixels: float, max_megapixels: float) -> WorkingImage:
    source = Path(path)
    target = _positive_megapixels(target_megapixels, "target_megapixels")
    maximum = _positive_megapixels(max_megapixels, "max_megapixels")
    if target > maximum:
        raise ValueError("target_megapixels must not exceed max_megapixels")
    if not source.is_file():
        raise FileNotFoundError(source)

    encoded = np.fromfile(source, dtype=np.uint8)
    if encoded.size == 0:
        raise ValueError(f"Image file is empty: {source}")
    source_sha256 = hashlib.sha256(encoded.tobytes()).hexdigest()
    is_jpeg = encoded.size >= 3 and encoded[:3].tobytes() == b"\xff\xd8\xff"
    decode_scale = 1.0
    decode_flag = cv2.IMREAD_COLOR
    if is_jpeg:
        source_width, source_height = _jpeg_dimensions(source)
        decode_flag, decode_scale = _reduced_jpeg_flag(source_width, source_height, target)

    pixels = cv2.imdecode(encoded, decode_flag)
    if pixels is None:
        raise ValueError(f"Unable to decode image: {source}")
    _validate_pixels(pixels)

    output_width, output_height = target_dimensions(pixels.shape[1], pixels.shape[0], target)
    if (output_width, output_height) != (pixels.shape[1], pixels.shape[0]):
        pixels = cv2.resize(pixels, (output_width, output_height), interpolation=cv2.INTER_AREA)
    if output_width * output_height > int(maximum * 1_000_000):
        raise ValueError("decoded working image exceeds max_megapixels")
    return WorkingImage(
        pixels=np.ascontiguousarray(pixels, dtype=np.uint8),
        width=output_width,
        height=output_height,
        decode_scale=decode_scale,
        source_sha256=source_sha256,
    )


def thumbnail_statistics(pixels: np.ndarray) -> ThumbnailStatistics:
    _validate_pixels(pixels)
    height, width = pixels.shape[:2]
    scale = min(1.0, 512.0 / max(height, width))
    if scale < 1.0:
        thumbnail = cv2.resize(
            pixels,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        thumbnail = pixels
    gray = cv2.cvtColor(thumbnail, cv2.COLOR_BGR2GRAY)
    lower, upper = np.percentile(gray, (5, 95))
    illumination = cv2.blur(gray, (33, 33))
    illumination_lower, illumination_upper = np.percentile(illumination, (5, 95))
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    saturation = cv2.cvtColor(thumbnail, cv2.COLOR_BGR2HSV)[:, :, 1]
    edges = cv2.Canny(gray, 80, 160)
    return ThumbnailStatistics(
        percentile_contrast=float(upper - lower),
        illumination_spread=float(illumination_upper - illumination_lower),
        laplacian_variance=laplacian_variance,
        saturation_median=float(np.median(saturation)),
        edge_density=float(np.count_nonzero(edges) / edges.size),
    )


def route_defects(pixels: np.ndarray) -> DefectRoute:
    stats = thumbnail_statistics(pixels)
    if stats.illumination_spread >= 42:
        return DefectRoute("shadow", True, 3)
    if stats.laplacian_variance <= 55:
        return DefectRoute("blurred", True, 3)
    if stats.percentile_contrast <= 58:
        return DefectRoute("low_contrast", True, 2)
    if stats.edge_density >= 0.19 and stats.saturation_median <= 18:
        return DefectRoute("line_art", False, 1)
    return DefectRoute("balanced", False, 1)


def _normalize_illumination(luminance: np.ndarray) -> np.ndarray:
    background = cv2.blur(luminance, (33, 33)).astype(np.float32)
    normalized = luminance.astype(np.float32) - background + float(np.median(background))
    return np.clip(normalized, 0, 255).astype(np.uint8)


def sharpest_variant_index(route: DefectRoute) -> int:
    if not isinstance(route, DefectRoute):
        raise TypeError("route must be a DefectRoute")
    variants = RECIPE_VARIANTS[route.name]
    return max(range(len(variants)), key=lambda index: (variants[index][1], variants[index][0]))


def _sharpen_text_edges(pixels: np.ndarray, amount: float) -> np.ndarray:
    coarse = cv2.GaussianBlur(pixels, (0, 0), sigmaX=0.9, sigmaY=0.9)
    sharpened = cv2.addWeighted(pixels, 1.0 + amount, coarse, -amount, 0)
    fine_amount = min(0.55, amount * 0.5)
    fine = cv2.GaussianBlur(sharpened, (0, 0), sigmaX=0.4, sigmaY=0.4)
    return cv2.addWeighted(sharpened, 1.0 + fine_amount, fine, -fine_amount, 0)


def apply_fast_recipe(pixels: np.ndarray, route: DefectRoute, variant: int) -> np.ndarray:
    _validate_pixels(pixels)
    if not isinstance(route, DefectRoute):
        raise TypeError("route must be a DefectRoute")
    if isinstance(variant, bool) or not isinstance(variant, int) or not 0 <= variant < route.candidate_count:
        raise ValueError(f"variant must be an integer from 0 to {route.candidate_count - 1}")
    clahe_clip, sharpen_amount, denoise_strength = RECIPE_VARIANTS[route.name][variant]

    lab = cv2.cvtColor(pixels, cv2.COLOR_BGR2LAB)
    luminance, channel_a, channel_b = cv2.split(lab)
    if route.name == "shadow":
        luminance = _normalize_illumination(luminance)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8, 8))
    luminance = clahe.apply(luminance)
    output = cv2.cvtColor(cv2.merge((luminance, channel_a, channel_b)), cv2.COLOR_LAB2BGR)

    if denoise_strength:
        diameter = 3 if denoise_strength <= 3 else 5
        output = cv2.bilateralFilter(
            output,
            diameter,
            sigmaColor=max(6, denoise_strength * 3),
            sigmaSpace=diameter,
        )
    output = _sharpen_text_edges(output, sharpen_amount)
    if route.name == "line_art":
        gray = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY)
        output = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return np.ascontiguousarray(output, dtype=np.uint8)


def rotate_with_white_canvas(pixels: np.ndarray, degrees: float) -> np.ndarray:
    _validate_pixels(pixels)
    if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or not math.isfinite(degrees):
        raise ValueError("degrees must be a finite real number")
    rounded = int(round(float(degrees))) % 360
    if abs(float(degrees) - round(float(degrees))) < 1e-6 and rounded % 90 == 0:
        if rounded == 0:
            return pixels.copy()
        rotate_code = {
            90: cv2.ROTATE_90_COUNTERCLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_CLOCKWISE,
        }[rounded]
        return np.ascontiguousarray(cv2.rotate(pixels, rotate_code), dtype=np.uint8)
    if float(degrees) % 360 == 0:
        return pixels.copy()

    height, width = pixels.shape[:2]
    center = ((width - 1) / 2.0, (height - 1) / 2.0)
    matrix = cv2.getRotationMatrix2D(center, float(degrees), 1.0)
    cosine = abs(matrix[0, 0])
    sine = abs(matrix[0, 1])
    output_width = max(1, math.ceil(height * sine + width * cosine))
    output_height = max(1, math.ceil(height * cosine + width * sine))
    matrix[0, 2] += (output_width - 1) / 2.0 - center[0]
    matrix[1, 2] += (output_height - 1) / 2.0 - center[1]
    output = cv2.warpAffine(
        pixels,
        matrix,
        (output_width, output_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return np.ascontiguousarray(output, dtype=np.uint8)


def _encode_jpeg(pixels: np.ndarray, quality: int) -> np.ndarray:
    succeeded, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not succeeded:
        raise ValueError(f"JPEG encoding failed at quality {quality}")
    return encoded


DELIVERY_JPEG_QUALITY = 88


def encode_delivery_jpeg(pixels: np.ndarray) -> tuple[bytes, int]:
    _validate_pixels(pixels)
    encoded = _encode_jpeg(pixels, DELIVERY_JPEG_QUALITY)
    payload = encoded.tobytes()
    del encoded
    return payload, DELIVERY_JPEG_QUALITY


def _sampled_edge_energy(pixels: np.ndarray) -> float:
    height, width = pixels.shape[:2]
    scale = min(1.0, 768.0 / max(height, width))
    if scale < 1.0:
        sample = cv2.resize(
            pixels,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        sample = pixels
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(cv2.magnitude(gradient_x, gradient_y)))


def _edge_energy_loss(reference_energy: float, candidate: np.ndarray) -> float:
    candidate_energy = _sampled_edge_energy(candidate)
    if reference_energy <= np.finfo(np.float32).eps:
        return 0.0
    return max(0.0, (reference_energy - candidate_energy) / reference_energy)


def encode_output(
    pixels: np.ndarray,
    destination: Path,
    quality_floor: EncodingQualityFloor | None,
) -> EncodedImage:
    _validate_pixels(pixels)
    output_path = Path(destination)
    if output_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("destination must use a .jpg or .jpeg extension")
    if output_path.exists():
        raise FileExistsError(f"destination already exists: {output_path}")
    floor = quality_floor if quality_floor is not None else EncodingQualityFloor()
    if not isinstance(floor, EncodingQualityFloor):
        raise TypeError("quality_floor must be an EncodingQualityFloor or None")

    reference_energy = _sampled_edge_energy(pixels)
    candidates: list[tuple[int, np.ndarray, float, bool, bool | None]] = []
    for quality in (88, 84, 80):
        encoded = _encode_jpeg(pixels, quality)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError(f"JPEG verification decode failed at quality {quality}")
        edge_loss = _edge_energy_loss(reference_energy, decoded)
        edge_floor_met = edge_loss <= floor.max_edge_energy_loss
        ocr_equality_passed = None
        if edge_floor_met and floor.ocr_equality_check is not None:
            ocr_equality_passed = bool(floor.ocr_equality_check(decoded, quality))
        candidates.append((quality, encoded, edge_loss, edge_floor_met, ocr_equality_passed))

    acceptable_lower = [
        candidate
        for candidate in candidates[1:]
        if candidate[3] and candidate[4] is not False
    ]
    if acceptable_lower:
        selected_candidate = min(acceptable_lower, key=lambda item: item[1].size)
    else:
        selected_candidate = candidates[0]
    quality, selected, selected_loss, edge_floor_met, ocr_equality_passed = selected_candidate
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = selected.tobytes()
    try:
        with output_path.open("xb") as handle:
            handle.write(output_bytes)
    except FileExistsError:
        raise FileExistsError(f"destination already exists: {output_path}") from None
    return EncodedImage(
        destination=output_path,
        quality=quality,
        size_bytes=len(output_bytes),
        sha256=hashlib.sha256(output_bytes).hexdigest(),
        sampled_edge_energy_loss=selected_loss,
        edge_floor_met=edge_floor_met,
        ocr_equality_passed=ocr_equality_passed,
    )

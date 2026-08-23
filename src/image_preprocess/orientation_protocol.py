from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path


MODEL_NAME = "PP-LCNet_x1_0_doc_ori"
INITIAL_CONFIDENCE_THRESHOLD = 0.85
VERIFICATION_CONFIDENCE_THRESHOLD = 0.80
ALLOWED_CORRECTIONS = {0, 90, 180, 270}
ALLOWED_STATUSES = {
    "VERIFIED",
    "UNAVAILABLE",
    "TIMED_OUT",
    "INVALID_RESULT",
    "INVALID_SOURCE",
    "LOW_CONFIDENCE",
    "VERIFICATION_FAILED",
    "MANUAL_OVERRIDE",
}


def _strict_angle(value: object, field_name: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value not in ALLOWED_CORRECTIONS:
        raise ValueError(f"invalid {field_name}")
    return value


def _strict_confidence(value: object, field_name: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    normalized = float(value)
    if not isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError(f"invalid {field_name}")
    return normalized


@dataclass(frozen=True)
class OrientationJob:
    source_path: Path
    model_root: Path
    initial_confidence_threshold: float = INITIAL_CONFIDENCE_THRESHOLD
    verification_confidence_threshold: float = VERIFICATION_CONFIDENCE_THRESHOLD

    def as_worker_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source_path"] = str(self.source_path)
        payload["model_root"] = str(self.model_root)
        return payload

    @classmethod
    def from_worker_payload(cls, payload: dict[str, object]) -> "OrientationJob":
        return cls(
            source_path=Path(str(payload["source_path"])),
            model_root=Path(str(payload["model_root"])),
            initial_confidence_threshold=float(
                payload.get("initial_confidence_threshold", INITIAL_CONFIDENCE_THRESHOLD)
            ),
            verification_confidence_threshold=float(
                payload.get("verification_confidence_threshold", VERIFICATION_CONFIDENCE_THRESHOLD)
            ),
        )


@dataclass(frozen=True)
class OrientationPrediction:
    degrees: int
    confidence: float


@dataclass(frozen=True)
class OrientationResult:
    status: str
    correction_degrees: int
    confidence: float
    verification_degrees: int | None
    verification_confidence: float | None
    model_name: str
    reason: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    def as_worker_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_worker_payload(cls, payload: dict[str, object]) -> "OrientationResult":
        status = str(payload["status"])
        correction = _strict_angle(payload["correction_degrees"], "correction angle")
        confidence = _strict_confidence(payload["confidence"], "confidence")
        raw_verification_degrees = payload.get("verification_degrees")
        raw_verification_confidence = payload.get("verification_confidence")
        verification_degrees = _strict_angle(
            raw_verification_degrees, "verification angle", allow_none=True
        )
        verification_confidence = _strict_confidence(
            raw_verification_confidence, "verification confidence", allow_none=True
        )
        model_name = str(payload["model_name"])
        reason = str(payload["reason"]) if payload.get("reason") is not None else None
        if status not in ALLOWED_STATUSES:
            raise ValueError("invalid orientation status")
        if status == "VERIFIED":
            if model_name != MODEL_NAME or verification_degrees != 0 or verification_confidence is None:
                raise ValueError("unverified orientation result")
            if confidence < INITIAL_CONFIDENCE_THRESHOLD:
                raise ValueError("verified initial confidence is below policy")
            if verification_confidence < VERIFICATION_CONFIDENCE_THRESHOLD:
                raise ValueError("verified post-rotation confidence is below policy")
        elif status == "MANUAL_OVERRIDE":
            if (
                model_name != "human"
                or confidence != 1.0
                or verification_degrees != 0
                or verification_confidence != 1.0
            ):
                raise ValueError("invalid manual orientation result")
        elif correction != 0:
            raise ValueError("non-verified orientation result cannot carry a correction")
        return cls(
            status=status,
            correction_degrees=correction,
            confidence=confidence,
            verification_degrees=verification_degrees,
            verification_confidence=verification_confidence,
            model_name=model_name,
            reason=reason,
        )

    @classmethod
    def degraded(cls, status: str, reason: str) -> "OrientationResult":
        return cls(status, 0, 0.0, None, None, MODEL_NAME, reason)

    @classmethod
    def manual(cls, correction_degrees: int) -> "OrientationResult":
        normalized = _strict_angle(correction_degrees, "manual orientation")
        assert normalized is not None
        return cls("MANUAL_OVERRIDE", normalized, 1.0, 0, 1.0, "human", None)

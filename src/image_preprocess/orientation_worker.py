from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, TextIO

import numpy as np
from PIL import Image

from .orientation_protocol import (
    ALLOWED_CORRECTIONS,
    INITIAL_CONFIDENCE_THRESHOLD,
    MODEL_NAME,
    VERIFICATION_CONFIDENCE_THRESHOLD,
    OrientationJob,
    OrientationPrediction,
    OrientationResult,
)


def parse_paddle_payload(payload: dict[str, object]) -> OrientationPrediction:
    """Convert PaddleX's document-orientation result to the strict local protocol."""
    result = payload.get("res")
    if not isinstance(result, dict):
        raise ValueError("missing Paddle result")
    labels = result.get("label_names")
    scores = result.get("scores")
    if not isinstance(labels, list) or not labels or not isinstance(scores, list) or not scores:
        raise ValueError("missing Paddle orientation label or score")
    try:
        degrees = int(str(labels[0]))
        confidence = float(scores[0])
    except (TypeError, ValueError) as error:
        raise ValueError("invalid Paddle orientation label or score") from error
    if degrees not in ALLOWED_CORRECTIONS:
        raise ValueError("invalid Paddle angle")
    # Reuse the protocol's finite/range checks instead of accepting NaN or >1.
    checked = OrientationResult.from_worker_payload(
        {
            "status": "LOW_CONFIDENCE",
            "correction_degrees": 0,
            "confidence": confidence,
            "verification_degrees": None,
            "verification_confidence": None,
            "model_name": MODEL_NAME,
            "reason": "PARSE_ONLY",
        }
    )
    return OrientationPrediction(degrees, checked.confidence)


def select_verified_correction(
    initial: OrientationPrediction,
    verifications: dict[int, OrientationPrediction],
    *,
    initial_threshold: float = INITIAL_CONFIDENCE_THRESHOLD,
    verification_threshold: float = VERIFICATION_CONFIDENCE_THRESHOLD,
) -> OrientationResult:
    """Select a clockwise correction only after a rotated copy re-predicts as 0 degrees."""
    if initial.degrees not in ALLOWED_CORRECTIONS:
        return OrientationResult.degraded("INVALID_RESULT", "PADDLE_RETURNED_UNKNOWN_ANGLE")
    if initial.confidence < initial_threshold:
        return OrientationResult(
            "LOW_CONFIDENCE",
            0,
            initial.confidence,
            None,
            None,
            MODEL_NAME,
            "INITIAL_CONFIDENCE_BELOW_THRESHOLD",
        )

    accepted: list[tuple[float, int, OrientationPrediction]] = []
    for correction, prediction in verifications.items():
        if correction not in ALLOWED_CORRECTIONS:
            continue
        if prediction.degrees == 0 and prediction.confidence >= verification_threshold:
            accepted.append((prediction.confidence, correction, prediction))
    if not accepted:
        best = max(verifications.values(), key=lambda item: item.confidence, default=None)
        return OrientationResult(
            "VERIFICATION_FAILED",
            0,
            initial.confidence,
            best.degrees if best is not None else None,
            best.confidence if best is not None else None,
            MODEL_NAME,
            "NO_ROTATED_COPY_VERIFIED_AS_UPRIGHT",
        )

    _, correction, verification = max(accepted, key=lambda item: (item[0], -item[1]))
    return OrientationResult(
        "VERIFIED",
        correction,
        initial.confidence,
        verification.degrees,
        verification.confidence,
        MODEL_NAME,
        None,
    )


def _result_payload(result: Any) -> dict[str, object]:
    raw = getattr(result, "json", None)
    if callable(raw):
        raw = raw()
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    if isinstance(raw, dict):
        return raw
    if isinstance(result, dict):
        return result

    with tempfile.TemporaryDirectory(prefix="orientation-result-") as temp_dir:
        output_path = Path(temp_dir) / "result.json"
        saver = getattr(result, "save_to_json", None)
        if not callable(saver):
            raise ValueError("unsupported Paddle result object")
        saver(str(output_path))
        parsed = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid saved Paddle result")
        return parsed


def _first_prediction(model: Any, image: np.ndarray) -> OrientationPrediction:
    predictions: Iterable[Any] = model.predict(image, batch_size=1)
    first = next(iter(predictions), None)
    if first is None:
        raise ValueError("Paddle returned no result")
    return parse_paddle_payload(_result_payload(first))


def _clockwise_quarter_turn(image: np.ndarray, degrees: int) -> np.ndarray:
    if degrees not in ALLOWED_CORRECTIONS:
        raise ValueError("invalid correction angle")
    return np.ascontiguousarray(np.rot90(image, k=-(degrees // 90)))


def _model_files_available(model_root: Path) -> bool:
    model_dir = model_root / MODEL_NAME
    required_model_files = (model_dir / "inference.json", model_dir / "inference.pdiparams")
    return all(path.is_file() for path in required_model_files)


def build_orientation_model(model_root: Path) -> Any:
    from paddleocr import DocImgOrientationClassification

    return DocImgOrientationClassification(
        model_name=MODEL_NAME,
        model_dir=str(model_root / MODEL_NAME),
        device="cpu",
    )


def _run_with_model(job: OrientationJob, model: Any) -> OrientationResult:
    try:
        with Image.open(job.source_path) as source:
            image = np.asarray(source.convert("RGB"))
        initial = _first_prediction(model, image)
        if initial.confidence < job.initial_confidence_threshold:
            return select_verified_correction(
                initial,
                {},
                initial_threshold=job.initial_confidence_threshold,
                verification_threshold=job.verification_confidence_threshold,
            )

        direct = initial.degrees
        inverse = (-initial.degrees) % 360
        candidates = dict.fromkeys((direct, inverse))
        verifications = {
            correction: _first_prediction(model, _clockwise_quarter_turn(image, correction))
            for correction in candidates
        }
        return select_verified_correction(
            initial,
            verifications,
            initial_threshold=job.initial_confidence_threshold,
            verification_threshold=job.verification_confidence_threshold,
        )
    except Exception as error:
        return OrientationResult.degraded(
            "INVALID_RESULT",
            f"PADDLE_ORIENTATION_INFERENCE_FAILED:{type(error).__name__}",
        )


def run_job(job: OrientationJob) -> OrientationResult:
    model_root = job.model_root / "official_models"
    if not _model_files_available(model_root):
        return OrientationResult.degraded("UNAVAILABLE", "PADDLE_ORIENTATION_MODEL_MISSING")
    try:
        # Paddle writes progress messages to stdout; keep stdout reserved for one JSON result.
        with contextlib.redirect_stdout(sys.stderr):
            job.model_root.mkdir(parents=True, exist_ok=True)
            model = build_orientation_model(model_root)
            return _run_with_model(job, model)
    except ImportError:
        return OrientationResult.degraded("UNAVAILABLE", "PADDLE_ORIENTATION_NOT_INSTALLED")
    except Exception as error:
        return OrientationResult.degraded(
            "INVALID_RESULT",
            f"PADDLE_ORIENTATION_INFERENCE_FAILED:{type(error).__name__}",
        )


def run_request(model: Any, request: dict[str, object]) -> dict[str, object]:
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
    raw_job = request.get("job")
    if not isinstance(raw_job, dict):
        raise ValueError("job must be an object")
    job = OrientationJob.from_worker_payload(raw_job)
    with contextlib.redirect_stdout(sys.stderr):
        result = _run_with_model(job, model)
    return {"request_id": request_id, **result.as_worker_payload()}


def serve_requests(model: Any, instream: TextIO, outstream: TextIO) -> int:
    for raw_line in instream:
        if not raw_line.strip():
            continue
        request: object = None
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = run_request(model, request)
        except Exception as error:
            request_id = request.get("request_id") if isinstance(request, dict) else None
            response = {
                "request_id": request_id,
                **OrientationResult.degraded(
                    "INVALID_RESULT", f"INVALID_REQUEST:{type(error).__name__}"
                ).as_worker_payload(),
            }
        outstream.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
        outstream.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--serve"]:
        model_root = Path(os.environ.get("PADDLE_OCR_BASE_DIR", "")) / "official_models"
        if not _model_files_available(model_root):
            print("PADDLE_ORIENTATION_MODEL_MISSING", file=sys.stderr)
            return 1
        try:
            with contextlib.redirect_stdout(sys.stderr):
                model = build_orientation_model(model_root)
        except ImportError:
            print("PADDLE_ORIENTATION_NOT_INSTALLED", file=sys.stderr)
            return 1
        except Exception as error:
            print(f"PADDLE_ORIENTATION_MODEL_FAILED:{type(error).__name__}", file=sys.stderr)
            return 1
        sys.stdout.write(json.dumps({"type": "READY"}) + "\n")
        sys.stdout.flush()
        return serve_requests(model, sys.stdin, sys.stdout)
    if len(arguments) == 2 and arguments[0] == "--job":
        job_argument = arguments[1]
    elif len(arguments) == 1:
        job_argument = arguments[0]
    else:
        print(json.dumps(OrientationResult.degraded("INVALID_RESULT", "JOB_ARGUMENT_REQUIRED").as_worker_payload()))
        return 2
    try:
        job_payload = json.loads(Path(job_argument).read_text(encoding="utf-8"))
        if not isinstance(job_payload, dict):
            raise ValueError("job must be an object")
        job = OrientationJob.from_worker_payload(job_payload)
        result = run_job(job)
    except Exception as error:
        result = OrientationResult.degraded("INVALID_RESULT", f"INVALID_JOB:{type(error).__name__}")
    print(json.dumps(result.as_worker_payload(), ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

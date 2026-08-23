from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .orientation_protocol import OrientationJob, OrientationResult


ProcessFactory = Callable[..., subprocess.Popen[str]]

CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 30.0


class OrientationWorkerRunner:
    def __init__(
        self,
        worker_python: Path,
        *,
        data_root: Path,
        model_root: Path,
        timeout_seconds: float = 30,
        startup_timeout_seconds: float = 120,
        shutdown_timeout_seconds: float = 1,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        self.worker_python = worker_python.resolve()
        self.data_root = data_root.resolve()
        self.model_root = model_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._process_factory = process_factory
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._responses: queue.Queue[str | BaseException] | None = None
        self._runtime_temp: Path | None = None
        self._lock = threading.RLock()
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None

    def __enter__(self) -> "OrientationWorkerRunner":
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def start(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            if self._process is not None and not self._stop_process():
                raise OSError("orientation worker runtime cleanup failed")
            process = self._launch_process()
            responses: queue.Queue[str | BaseException] = queue.Queue()
            reader = threading.Thread(
                target=self._read_responses,
                args=(process, responses),
                name="orientation-worker-reader",
                daemon=True,
            )
            self._process = process
            self._responses = responses
            self._reader_thread = reader
            reader.start()
            try:
                ready = self._next_response(self.startup_timeout_seconds)
                payload = json.loads(ready)
                if payload != {"type": "READY"}:
                    raise ValueError("orientation worker did not emit READY")
            except BaseException:
                self._stop_process()
                raise

    def predict(self, source_path: Path) -> OrientationResult:
        resolved_source = source_path.resolve()
        if not resolved_source.is_file() or not resolved_source.is_relative_to(self.data_root):
            return OrientationResult.degraded(
                "INVALID_SOURCE", "PADDLE_ORIENTATION_SOURCE_OUTSIDE_DATA_ROOT"
            )
        if not self.worker_python.is_file():
            return OrientationResult.degraded("UNAVAILABLE", "PADDLE_ORIENTATION_WORKER_UNAVAILABLE")

        with self._lock:
            if self._circuit_is_open():
                return OrientationResult.degraded("UNAVAILABLE", "PADDLE_ORIENTATION_CIRCUIT_OPEN")
            final_status = "UNAVAILABLE"
            final_reason = "PADDLE_ORIENTATION_WORKER_UNAVAILABLE"
            for attempt in range(2):
                try:
                    self.start()
                    result = self._predict_once(resolved_source)
                    self._record_success()
                    return result
                except TimeoutError:
                    final_status = "TIMED_OUT"
                    final_reason = "PADDLE_ORIENTATION_TIMEOUT"
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    final_status = "INVALID_RESULT"
                    final_reason = "PADDLE_ORIENTATION_PROTOCOL_INVALID"
                except (BrokenPipeError, EOFError, OSError):
                    final_status = "UNAVAILABLE"
                    final_reason = "PADDLE_ORIENTATION_WORKER_UNAVAILABLE"

                cleanup_ok = self._stop_process()
                if not cleanup_ok:
                    self._record_failure()
                    return OrientationResult.degraded(
                        "UNAVAILABLE", "PADDLE_ORIENTATION_TEMP_CLEANUP_FAILED"
                    )
                if attempt == 1:
                    self._record_failure()
                    return OrientationResult.degraded(final_status, final_reason)

        return OrientationResult.degraded(final_status, final_reason)

    def _circuit_is_open(self) -> bool:
        if self._circuit_opened_at is None:
            return False
        return (time.monotonic() - self._circuit_opened_at) < CIRCUIT_COOLDOWN_SECONDS

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_opened_at = None

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_opened_at = time.monotonic()

    def close(self) -> None:
        with self._lock:
            self._stop_process()

    def _launch_process(self) -> subprocess.Popen[str]:
        staging_dir = self.data_root / "staging" / "orientation"
        runtime_temp = self.data_root / "runtime-temp" / "orientation" / uuid.uuid4().hex
        runtime_cache = self.data_root / "runtime-cache" / "orientation"
        runtime_paths = {
            "staging directory": staging_dir,
            "PIP_CACHE_DIR": runtime_cache / "pip",
            "XDG_CACHE_HOME": runtime_cache / "xdg",
            "UV_CACHE_DIR": runtime_cache / "uv",
            "PYTHONPYCACHEPREFIX": runtime_cache / "pycache",
            "PADDLE_OCR_BASE_DIR": self.model_root,
            "PADDLE_PDX_CACHE_HOME": self.model_root,
            "TEMP": runtime_temp,
            "TMP": runtime_temp,
        }
        for label, path in runtime_paths.items():
            if path.resolve().drive.upper() != "D:":
                raise ValueError(f"{label} must be on drive D: {path.resolve()}")

        staging_dir.mkdir(parents=True, exist_ok=True)
        runtime_temp.mkdir(parents=True, exist_ok=True)
        for path in runtime_paths.values():
            path.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)

        package_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_root)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        for variable in (
            "PIP_CACHE_DIR",
            "XDG_CACHE_HOME",
            "UV_CACHE_DIR",
            "PYTHONPYCACHEPREFIX",
            "PADDLE_OCR_BASE_DIR",
            "PADDLE_PDX_CACHE_HOME",
            "TEMP",
            "TMP",
        ):
            environment[variable] = str(runtime_paths[variable].resolve())
        environment["PADDLE_PDX_MODEL_SOURCE"] = "BOS"
        environment["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        try:
            process = self._process_factory(
                [str(self.worker_python), "-m", "image_lab.orientation_worker", "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=staging_dir,
                env=environment,
            )
        except BaseException:
            shutil.rmtree(runtime_temp, ignore_errors=True)
            raise
        self._runtime_temp = runtime_temp
        return process

    def _predict_once(self, source_path: Path) -> OrientationResult:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None or process.stdout is None:
            raise BrokenPipeError("orientation worker is not available")

        request_id = uuid.uuid4().hex
        request = {
            "request_id": request_id,
            "job": OrientationJob(source_path, self.model_root).as_worker_payload(),
        }
        process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
        process.stdin.flush()
        raw_line = self._next_response(self.timeout_seconds)
        payload = json.loads(raw_line)
        if not isinstance(payload, dict) or payload.get("request_id") != request_id:
            raise ValueError("orientation response does not match request")
        result_payload = dict(payload)
        result_payload.pop("request_id")
        return OrientationResult.from_worker_payload(result_payload)

    @staticmethod
    def _read_responses(
        process: subprocess.Popen[str], responses: queue.Queue[str | BaseException]
    ) -> None:
        if process.stdout is None:
            responses.put(EOFError("orientation worker stdout is unavailable"))
            return
        while True:
            try:
                response = process.stdout.readline()
            except BaseException as error:
                responses.put(error)
                return
            responses.put(response)
            if response == "":
                return

    def _next_response(self, timeout_seconds: float) -> str:
        responses = self._responses
        if responses is None:
            raise EOFError("orientation worker response queue is unavailable")
        try:
            response = responses.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise TimeoutError("orientation worker response timed out") from error
        if isinstance(response, BaseException):
            raise OSError("orientation worker response failed") from response
        if response == "":
            raise EOFError("orientation worker closed stdout")
        return response

    def _stop_process(self) -> bool:
        process = self._process
        reader = self._reader_thread
        process_stopped = process is None
        if process is not None:
            try:
                running = process.poll() is None
            except OSError:
                running = True
            if running:
                try:
                    process.terminate()
                except (OSError, subprocess.SubprocessError):
                    pass
                try:
                    process.wait(timeout=self.shutdown_timeout_seconds)
                    process_stopped = True
                except (OSError, subprocess.SubprocessError):
                    try:
                        process.kill()
                    except (OSError, subprocess.SubprocessError):
                        pass
                    try:
                        process.wait(timeout=self.shutdown_timeout_seconds)
                        process_stopped = True
                    except (OSError, subprocess.SubprocessError):
                        process_stopped = False
            else:
                process_stopped = True

            if process_stopped and process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        if reader is not None:
            reader.join(timeout=self.shutdown_timeout_seconds)
        reader_stopped = reader is None or not reader.is_alive()
        if process is not None and reader_stopped and process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

        self._process = None
        self._reader_thread = None
        self._responses = None

        runtime_temp = self._runtime_temp
        self._runtime_temp = None
        runtime_cleaned = True
        if runtime_temp is not None and runtime_temp.exists():
            try:
                shutil.rmtree(runtime_temp)
            except OSError:
                runtime_cleaned = False

        return process_stopped and reader_stopped and runtime_cleaned
